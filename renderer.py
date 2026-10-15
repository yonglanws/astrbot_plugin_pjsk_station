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


def _draw_source_badge(draw: ImageDraw.ImageDraw, renderer: _TextRenderer,
                       x: int, y: int, source: str, font: ImageFont.FreeTypeFont,
                       max_w: float) -> None:
    """来源标签,画成小圆角胶囊。放不下就整体省略。"""
    if not source:
        return
    text = source
    if font.getlength(text) + 14 > max_w:
        text = _truncate(text, font, max(0, max_w - 14))
        if not text:
            return
    pad_x, pad_y = 6, 2
    w = font.getlength(text) + pad_x * 2
    h = font.size + pad_y * 2
    draw.rounded_rectangle(
        [(x, y), (x + w, y + h)], radius=h / 2, fill="#F2F2F7",
    )
    renderer.text((x + pad_x, y + pad_y), text, TEXT_SECONDARY, font)


def render_rooms(rooms: list[RoomInfo], title: str, source_url: str) -> str:
    """渲染房间列表,返回临时 PNG 文件路径(调用方负责删除)。"""
    font_title = _load_font(28)
    font_sub = _load_font(14)
    font_number = _load_font(30)
    font_desc = _load_font(17)
    font_meta = _load_font(14)
    font_badge = _load_font(12)
    font_tiny = _load_font(12)

    header_h = 92
    number_w = font_number.getlength("99999") + 14
    time_w = font_meta.getlength("999分钟前")
    content_x = MARGIN + number_w + 16
    content_w = WIDTH - MARGIN - content_x - time_w - 14

    row_heights = _layout(rooms, font_desc, content_w)
    footer_h = 48
    total_height = header_h + sum(row_heights) + footer_h
    img = Image.new("RGB", (WIDTH, total_height), BG_COLOR)
    draw = ImageDraw.Draw(img)
    renderer = _TextRenderer(draw)

    # 头部:左侧竖条 + 标题 + 副标题,底部一条细分隔线
    draw.rounded_rectangle(
        [(MARGIN, 22), (MARGIN + 4, 46)], radius=2, fill=ACCENT_COLOR,
    )
    renderer.text((MARGIN + 14, 18), title, TEXT_PRIMARY, font_title)
    now_str = datetime.now().strftime("%H:%M:%S")
    renderer.text(
        (MARGIN + 14, 54),
        f"{len(rooms)} 个房间 · 更新于 {now_str}",
        TEXT_SECONDARY, font_sub,
    )
    draw.line([(MARGIN, 76), (WIDTH - MARGIN, 76)], fill=LINE_COLOR, width=1)

    y = header_h
    for i, room in enumerate(rooms):
        # 房号:胶囊底色 + 主色数字
        num_h = font_number.size + 12
        draw.rounded_rectangle(
            [(MARGIN, y + 14), (MARGIN + number_w, y + 14 + num_h)],
            radius=8, fill="#F0F6FF",
        )
        num_w = font_number.getlength(room.number)
        renderer.text(
            (MARGIN + (number_w - num_w) / 2, y + 20),
            room.number, ACCENT_COLOR, font_number,
        )

        # 用户名与来源徽章共享一行:用户名先截断,徽章只用剩下的宽度
        badge_w = font_badge.getlength(room.source) + 20 if room.source else 0
        meta = _truncate(room.username, font_meta, max(40, content_w - badge_w - 8))
        meta_w = font_meta.getlength(meta)
        renderer.text((content_x, y + 10), meta, TEXT_SECONDARY, font_meta)
        if room.source and meta_w + 8 + badge_w <= content_w:
            _draw_source_badge(
                draw, renderer,
                content_x + meta_w + 8, y + 8,
                room.source, font_badge, content_w - meta_w - 8,
            )

        desc_y = y + 36
        for line in _wrap_text(room.raw, font_desc, content_w):
            renderer.text((content_x, desc_y), line, TEXT_PRIMARY, font_desc)
            desc_y += 26

        renderer.text(
            (WIDTH - MARGIN - time_w, y + 12),
            room.time, TEXT_SECONDARY, font_meta,
        )

        y += row_heights[i]
        if i < len(rooms) - 1:
            draw.line([(MARGIN, y - 1), (WIDTH - MARGIN, y - 1)], fill=LINE_COLOR, width=1)

    draw.line([(MARGIN, y + 8), (WIDTH - MARGIN, y + 8)], fill=LINE_COLOR, width=1)
    renderer.text((MARGIN, y + 16), REPO_URL, TEXT_TERTIARY, font_tiny)
    renderer.text((MARGIN, y + 32), source_url, TEXT_TERTIARY, font_tiny)

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(tmp.name, "PNG")
    tmp.close()
    return tmp.name
