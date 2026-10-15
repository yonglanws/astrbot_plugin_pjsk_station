"""把房间列表画成一张图片。

同步函数,调用方应放进 asyncio.to_thread,避免阻塞 bot 事件循环。

emoji 用本地彩色字体(Windows 的 seguiemj.ttf、Linux 的 NotoColorEmoji)
绘制,不依赖 pilmoji 的外网 CDN。
"""

import tempfile
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .station_api import RoomInfo

WIDTH = 620
MARGIN = 26
ROW_HEIGHT = 76
LINE_COLOR = "#E5E5EA"
BG_COLOR = "#FFFFFF"
TEXT_PRIMARY = "#1D1D1F"
TEXT_SECONDARY = "#6E6E73"
TEXT_TERTIARY = "#86868B"
ACCENT_COLOR = "#007AFF"

REPO_URL = "https://github.com/yonglanws/astrbot_plugin_pjsk_station"

_FALLBACK_FONT_PATHS = [
    str(Path(__file__).parent / "font.ttf"),
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/arial.ttf",
]

# 彩色 emoji 字体:装了就本地绘制,找不到就退回 pilmoji(需要联网 CDN)
_EMOJI_FONT_PATHS = [
    "C:/Windows/Fonts/seguiemj.ttf",
    "/usr/share/fonts/opentype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/System/Library/Fonts/Apple Color Emoji.ttc",
]

_font_cache: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}
_emoji_font_path: str | None = None


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    key = (size, False)
    cached = _font_cache.get(key)
    if cached is not None:
        return cached
    for path in _FALLBACK_FONT_PATHS:
        try:
            font = ImageFont.truetype(path, size)
            _font_cache[key] = font
            return font
        except Exception:
            continue
    return ImageFont.load_default()


def _load_emoji_font_path() -> str | None:
    global _emoji_font_path
    if _emoji_font_path is not None:
        return _emoji_font_path
    for path in _EMOJI_FONT_PATHS:
        try:
            ImageFont.truetype(path)
            _emoji_font_path = path
            return path
        except Exception:
            continue
    return None


def _emoji_font_for(size: int) -> ImageFont.FreeTypeFont | None:
    """按正文字号加载彩色 emoji 字体(emoji 位图本身偏小,放大一点)。"""
    path = _load_emoji_font_path()
    if path is None:
        return None
    key = (size, True)
    cached = _font_cache.get(key)
    if cached is not None:
        return cached
    try:
        font = ImageFont.truetype(path, int(size * 1.25))
        _font_cache[key] = font
        return font
    except Exception:
        return None


_EMOJI_RANGES = (
    (0x1F300, 0x1FAFF),  # 杂项符号与象形文字、补充象形文字
    (0x2600, 0x27BF),    # 杂项符号、装饰符号
    (0x1F000, 0x1F2FF),  # 麻将牌等
    (0xFE0F, 0xFE0F),    # 变体选择符-16
)


def _is_emoji(ch: str) -> bool:
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in _EMOJI_RANGES)


class _TextRenderer:
    """逐段绘制文本:emoji 用彩色字体,其余用普通字体。

    彩色 emoji 字体里不含中日韩文字,普通字体里不含 emoji,
    按字符拆分可以两者都用上,并且不依赖任何网络请求。
    """

    def __init__(self, draw: ImageDraw.ImageDraw):
        self.draw = draw

    def _segment(self, text: str, font: ImageFont.FreeTypeFont):
        """把文本切成 (片段, 是否是emoji) 序列,emoji 字体找不到时整体不切。"""
        emoji_font = _emoji_font_for(font.size)
        if emoji_font is None:
            return [(text, False)]
        segments = []
        buf = ""
        buf_is_emoji = False
        for ch in text:
            ch_is_emoji = _is_emoji(ch)
            if buf and ch_is_emoji != buf_is_emoji:
                segments.append((buf, buf_is_emoji))
                buf = ""
            buf += ch
            buf_is_emoji = ch_is_emoji
        if buf:
            segments.append((buf, buf_is_emoji))
        return segments

    def text(self, xy, text, fill, font) -> None:
        x, y = xy
        segments = self._segment(text, font)
        if len(segments) == 1 and not segments[0][1]:
            self.draw.text((x, y), text, font=font, fill=fill)
            return
        for segment, is_emoji in segments:
            seg_font = _emoji_font_for(font.size) if is_emoji else font
            # 彩色 emoji 位图比文字小,放大的同时上移,让视觉中心对齐正文
            seg_y = y - (font.size * 0.15) if is_emoji else y
            try:
                self.draw.text((x, seg_y), segment, font=seg_font, fill=fill,
                               embedded_color=is_emoji)
            except Exception:
                self.draw.text((x, y), segment, font=font, fill=fill)
            x += seg_font.getlength(segment)


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    if not text:
        return [""]
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for ch in paragraph:
            test = current + ch
            if font.getlength(test) <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = ch
        if current:
            lines.append(current)
    return lines or [text]


def _truncate(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    if font.getlength(text) <= max_width:
        return text
    ellipsis = "…"
    while text and font.getlength(text + ellipsis) > max_width:
        text = text[:-1]
    return text + ellipsis if text else ""


def _layout(rooms: list[RoomInfo], font_desc: ImageFont.FreeTypeFont, content_w: int):
    """每行高度:顶部偏移 + 描述行 + 底部间距,不低于最小行高。"""
    row_heights = []
    for room in rooms:
        desc_lines = _wrap_text(room.raw, font_desc, content_w)
        needed = 36 + len(desc_lines) * 26 + 14
        row_heights.append(max(ROW_HEIGHT, needed))
    return row_heights

