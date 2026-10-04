"""车站数据源。

国服(Sekai Station, pjsk-zh.mid.red)和日服(プロセカ Station, pjsk.mid.red)
都不再走无头浏览器:国服用 SSE,日服用 WebSocket,直接拿到结构化数据。

协议是从前端 bundle 里还原的:
- 国服连接 /realtime 后服务端回放最近的房间,前端在 800ms 后取快照,
  只展示 expireTime(默认 300 秒)内的房间,按时间倒序。
- 日服连接后发送 setClient + getRoomNumberList,服务端分批推送全量房间,
  前端只保留 10 分钟(6e5 毫秒)内的。
"""

import asyncio
import json
import time
import unicodedata
from dataclasses import dataclass

import httpx

CN_API_BASE = "https://api-sekai-cn.hinix.cc:11565/station/api/v2"
CN_REALTIME_URL = f"{CN_API_BASE}/realtime"
# 与国服前端默认设置一致(pjsk-expire-time 默认 300 秒)
CN_EXPIRE_SECONDS = 300
CN_SNAPSHOT_DELAY = 0.8

JP_WS_URL = "wss://api-pjsekai.mid.red/station"
# 与日服前端一致:超过 10 分钟的房间会被移出列表
JP_EXPIRE_MS = 600_000
JP_LIST_QUIET_SECONDS = 1.5

_SYSTEM_KEYWORDS = ("系统", "公告", "admin", "官方", "announcement")


@dataclass
class RoomInfo:
    number: str
    raw: str
    username: str
    time: str
    timestamp: int  # unix 秒,用于排序


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


def _sort_and_filter(rooms: list[RoomInfo]) -> list[RoomInfo]:
    rooms.sort(key=lambda r: r.timestamp, reverse=True)
    return [r for r in rooms if r.number.isdigit() and not _is_system_message(r.username, r.raw)]


async def fetch_cn_rooms() -> list[RoomInfo]:
    """连接国服 SSE,收集回放快照并返回 5 分钟内的房间。"""
    rooms: dict[str, RoomInfo] = {}
    now = time.time()

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None), verify=False) as client:
        async with client.stream("GET", CN_REALTIME_URL) as resp:
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

    return _sort_and_filter(list(rooms.values()))


def _ingest_cn_room(payload: str, rooms: dict[str, RoomInfo], now: float) -> None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return
    # ["room", unix秒, 房号, "文案 / 频道", 用户名, 来源, [info]]
    if not isinstance(data, list) or len(data) < 5 or data[0] != "room":
        return
    timestamp, number = data[1], str(data[2])
    if not isinstance(timestamp, (int, float)) or now - timestamp > CN_EXPIRE_SECONDS:
        return
    rooms[f"{number}-{timestamp}"] = RoomInfo(
        number=number,
        raw=_normalize_digits(str(data[3])),
        username=str(data[4]),
        time=_format_relative_time(int(now - timestamp)),
        timestamp=int(timestamp),
    )


async def fetch_jp_rooms() -> list[RoomInfo]:
    """连接日服 WebSocket,取回全量房间列表并保留 10 分钟内的。"""
    import websockets

    rooms: dict[str, RoomInfo] = {}
    now_ms = time.time() * 1000

    async with websockets.connect(JP_WS_URL, open_timeout=10) as ws:
        await ws.send(json.dumps({
            "action": "setClient",
            "data": {"client": "PRSKStation", "send_room_number": True},
        }))
        await ws.send(json.dumps({"action": "getRoomNumberList", "data": None}))

        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=JP_LIST_QUIET_SECONDS)
            except asyncio.TimeoutError:
                break
            _ingest_jp_message(msg, rooms, now_ms)

    return _sort_and_filter(list(rooms.values()))


def _ingest_jp_message(msg: str, rooms: dict[str, RoomInfo], now_ms: float) -> None:
    try:
        data = json.loads(msg)
    except json.JSONDecodeError:
        return
    if data.get("action") != "sendRoomNumberList":
        return
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
        )
