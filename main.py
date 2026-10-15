import asyncio
import os

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .renderer import render_rooms
from .station_api import fetch_cn_rooms, fetch_jp_rooms

# 同时只跑一个取数+渲染,避免多人触发时打爆上游接口
_fetch_lock = asyncio.Lock()

_SERVERS = {
    "cn": ("PJSK国服车站", fetch_cn_rooms, "https://pjsk-zh.mid.red/"),
    "jp": ("PJSK日服车站", fetch_jp_rooms, "https://pjsk.mid.red/"),
}


async def _fetch_and_render(server: str) -> str:
    title, fetcher, source_url = _SERVERS[server]
    async with _fetch_lock:
        rooms = await fetcher()
        if not rooms:
            raise RuntimeError("未获取到房间数据")
        return await asyncio.to_thread(render_rooms, rooms, title, source_url)


@register(
    "astrbot_plugin_pjsk_station",
    "astrbot_plugin_pjsk_station",
    "查询 Proseka Station 实时房间列表",
    "1.2.0",
)
class PJSKStationPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        text = event.message_str.strip()
        if text in {"pjsk车站", "车站", "车牌列表", "查车站", "ycm"}:
            server = "cn"
        elif text == "jpycm":
            server = "jp"
        else:
            return
        logger.info(f"收到 PJSK Station 触发消息: {text} -> {server}")
        yield event.plain_result("正在获取房间列表...")
        filepath = ""
        try:
            filepath = await _fetch_and_render(server)
            yield event.image_result(filepath)
        except Exception as e:
            logger.error(f"PJSK Station 获取失败: {e}")
            yield event.plain_result("获取失败,请稍后再试")
        finally:
            if filepath and os.path.exists(filepath):
                try:
                    os.unlink(filepath)
                except Exception:
                    pass
