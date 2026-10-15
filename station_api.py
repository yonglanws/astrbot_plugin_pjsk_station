"""车站数据源。

国服(Sekai Station, pjsk-zh.mid.red)和日服(プロセカ Station, pjsk.mid.red)
都不用无头浏览器:国服用 SSE,日服用 WebSocket,直接拿结构化数据。

协议还原自前端 bundle,两端都换过一次家,当前版本:
- 国服原域名 api-sekai-cn.hinix.cc:11565 已下线,现用
  cn-sekai-station.haruki.seiunx.com,SSE 需带 client_id(UUID)参数。
  房间推送为 JSON 对象,前端 onopen 后 50ms 取快照,只展示 300 秒内的房间。
- 日服 wss://api-pjsekai.mid.red/station:连接后发 setClient + getRoomNumberList,
  服务端在约 2.7 秒后把全量列表分批发过来,前端只保留 10 分钟内的。
  该服务端限流,握手超时需重试。
"""

import asyncio
import json
import time
import unicodedata
import uuid
from dataclasses import dataclass

import httpx

CN_API_BASE = "https://cn-sekai-station.haruki.seiunx.com/station/api/v2"
CN_REALTIME_URL = f"{CN_API_BASE}/realtime"
# 与国服前端一致:onopen 后 50ms 取快照,只展示 300 秒内的房间
CN_SNAPSHOT_DELAY = 0.05
CN_EXPIRE_SECONDS = 300

JP_WS_URL = "wss://api-pjsekai.mid.red/station"
# 与日服前端一致:超过 10 分钟(6e5 毫秒)的房间会被移出列表
JP_EXPIRE_MS = 600_000
# 服务端约 2.7 秒后才推全量列表,收到列表后再等这么久收增量
JP_LIST_QUIET_SECONDS = 3.0
JP_CONNECT_TIMEOUT = 15.0
JP_MAX_ATTEMPTS = 3

_SYSTEM_KEYWORDS = ("系统", "公告", "admin", "官方", "announcement")


@dataclass
class RoomInfo:
    number: str
    raw: str
    username: str
    time: str
    timestamp: int  # unix 秒,用于排序
    source: str = ""  # 来源频道/账号,如 robo、haruki_xxxx、X@xxx


def _normalize_digits(text: str) -> str:
    """把 Unicode 数学粗体/双线体等特殊数字转成普通 ASCII 数字。"""
    result = []
    for ch in text:
        if unicodedata.category(ch) == "Nd" and ord(ch) > 0x39:
            result.append(str(unicodedata.digit(ch)))
        else:
            result.append(ch)
    return "".join(result)


def _is_system_message(username: str, raw: str) -> bool:
    combined = (username + raw).lower()
    return any(kw in combined for kw in _SYSTEM_KEYWORDS)


def _format_relative_time(seconds: int) -> str:
    seconds = max(0, seconds)
    if seconds < 60:
        return f"{seconds}秒前"
    if seconds < 3600:
        return f"{seconds // 60}分钟前"
    if seconds < 86400:
        return f"{seconds // 3600}小时前"
    return f"{seconds // 86400}天前"


def _finalize(rooms: dict[str, RoomInfo]) -> list[RoomInfo]:
    rooms = list(rooms.values())
    rooms.sort(key=lambda r: r.timestamp, reverse=True)
    return [
        r for r in rooms
        if r.number.isdigit() and not _is_system_message(r.username, r.raw)
    ]


async def fetch_cn_rooms() -> list[RoomInfo]:
    """连接国服 SSE,收集快照并返回 5 分钟内的房间。"""
    rooms: dict[str, RoomInfo] = {}
    now = time.time()

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None), verify=False) as client:
        async with client.stream(
            "GET", f"{CN_REALTIME_URL}?client_id={uuid.uuid4()}"
        ) as resp:
            resp.raise_for_status()
            event_name = ""
            data_lines: list[str] = []

            async def consume():
                nonlocal event_name
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].strip())
                    elif line == "":
                        if event_name == "room" and data_lines:
                            _ingest_cn_room("\n".join(data_lines), rooms, now)
                        event_name = ""
                        data_lines.clear()

            try:
                await asyncio.wait_for(consume(), timeout=CN_SNAPSHOT_DELAY)
            except asyncio.TimeoutError:
                pass

    return _finalize(rooms)


def _ingest_cn_room(payload: str, rooms: dict[str, RoomInfo], now: float) -> None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return
    # {"time": unix秒, "id": 房号, "msg": 文案, "name": 用户名, "source": 来源}
    if not isinstance(data, dict):
        return
    timestamp = data.get("time")
    number = str(data.get("id", ""))
    if not isinstance(timestamp, (int, float)) or now - timestamp > CN_EXPIRE_SECONDS:
        return
    rooms[f"{number}-{timestamp}"] = RoomInfo(
        number=number,
        raw=_normalize_digits(str(data.get("msg", ""))),
        username=str(data.get("name", "")),
        time=_format_relative_time(int(now - timestamp)),
        timestamp=int(timestamp),
        source=str(data.get("source", "")),
    )


async def fetch_jp_rooms() -> list[RoomInfo]:
    """连接日服 WebSocket,取回房间列表并保留 10 分钟内的。

    服务端握手偶发超时,重试若干次。
    """
    import websockets

    last_error: Exception | None = None
    for attempt in range(JP_MAX_ATTEMPTS):
        try:
            return await _fetch_jp_once(websockets)
        except Exception as e:  # noqa: BLE001 - 需要重试所有连接类错误
            last_error = e
            if attempt < JP_MAX_ATTEMPTS - 1:
                await asyncio.sleep(2)
    raise last_error  # type: ignore[misc]


async def _fetch_jp_once(websockets) -> list[RoomInfo]:
    rooms: dict[str, RoomInfo] = {}
    now_ms = time.time() * 1000

    async with websockets.connect(JP_WS_URL, open_timeout=JP_CONNECT_TIMEOUT) as ws:
        await ws.send(json.dumps({
            "action": "setClient",
            "data": {"client": "PRSKStation", "send_room_number": True},
        }))
        await ws.send(json.dumps({"action": "getRoomNumberList", "data": None}))

        got_list = False
        while True:
            try:
                msg = await asyncio.wait_for(
                    ws.recv(), timeout=JP_LIST_QUIET_SECONDS if got_list else JP_CONNECT_TIMEOUT
                )
            except asyncio.TimeoutError:
                break
            got_list = _ingest_jp_message(msg, rooms, now_ms) or got_list

    return _finalize(rooms)


def _ingest_jp_message(msg: str, rooms: dict[str, RoomInfo], now_ms: float) -> bool:
    """解析一条日服消息,返回是否拿到了房间列表。"""
    try:
        data = json.loads(msg)
    except json.JSONDecodeError:
        return False
    if data.get("action") != "sendRoomNumberList":
        return False
    for item in data.get("response") or []:
        source = item.get("source_info") or {}
        if source.get("type") == "system":
            continue
        timestamp_ms = item.get("time") or 0
        if now_ms - timestamp_ms > JP_EXPIRE_MS:
            continue
        number = str(item.get("number", ""))
        user = item.get("user_info") or {}
        rooms[f"{number}-{timestamp_ms}"] = RoomInfo(
            number=number,
            raw=_normalize_digits(str(item.get("raw_message", ""))),
            username=str(user.get("username", "")),
            time=_format_relative_time(int((now_ms - timestamp_ms) / 1000)),
            timestamp=int(timestamp_ms / 1000),
            source=str(source.get("name", "")),
        )
    return True
