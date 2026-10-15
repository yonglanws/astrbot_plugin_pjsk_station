"""把房间列表画成一张图片。

同步函数,调用方应放进 asyncio.to_thread,避免阻塞 bot 事件循环。

emoji 绘制优先用 pilmoji(从 CDN 取彩色贴图,和原来一样);
系统里装了彩色 emoji 字体(Windows seguiemj、Linux NotoColorEmoji)时
改用本地字体,省掉网络请求。
"""

import tempfile
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .station_api import RoomInfo

try:
    from pilmoji import Pilmoji
except ImportError:
    Pilmoji = None

WIDTH = 620
MARGIN = 22
ROW_HEIGHT = 74
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

# 本地彩色 emoji 字体:装了就用,能省掉 pilmoji 的 CDN 请求
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
    """文本绘制,pilmoji 优先,本地彩色字体兜底。

    pilmoji 从 CDN 取彩色 emoji 贴图(和插件原来的做法一致);
    没装 pilmoji 或 CDN 不可达时,若系统里有彩色 emoji 字体
    (Windows seguiemj、Linux NotoColorEmoji),按字符拆分本地绘制。
    """

    def __init__(self, img: Image.Image, draw: ImageDraw.ImageDraw):
        self.img = img
        self.draw = draw
        self._pilmoji = None
        self._pilmoji_failed = False
        # 本地有彩色 emoji 字体时不启用 pilmoji:省掉每个 emoji 一次 CDN 请求
        if Pilmoji is not None and _emoji_font_for(16) is None:
            try:
                self._pilmoji = Pilmoji(img)
                self._pilmoji.__enter__()
            except Exception:
                self._pilmoji = None

    def close(self) -> None:
        if self._pilmoji is not None:
            try:
                self._pilmoji.__exit__(None, None, None)
            except Exception:
                pass
            self._pilmoji = None

    def _fallback_text(self, xy, text, fill, font) -> None:
        """pilmoji 不可用时的本地绘制。"""
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

    def text(self, xy, text, fill, font) -> None:
        if self._pilmoji is not None:
            try:
                self._pilmoji.text(xy, text, fill, font)
                return
            except Exception:
                # pilmoji 首次失败多是 CDN 不可达,之后不再重试
                if not self._pilmoji_failed:
                    self._pilmoji_failed = True
                    self.close()
        self._fallback_text(xy, text, fill, font)

    @staticmethod
    def _segment(text: str, font: ImageFont.FreeTypeFont):
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
        needed = 38 + len(desc_lines) * 25 + 12
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
        [(x, y), (x + w, y + h)], radius=h / 2, fill="#EFEFF4",
    )
    renderer.text((x + pad_x, y + pad_y), text, TEXT_SECONDARY, font)


def render_rooms(rooms: list[RoomInfo], title: str, source_url: str) -> str:
    """渲染房间列表,返回临时 PNG 文件路径(调用方负责删除)。

    emoji 绘制:系统有彩色 emoji 字体就用本地绘制(快,无网络请求);
    没有才退回 pilmoji 从 CDN 取贴图(慢,但 emoji 更精致)。
    """
    font_title = _load_font(28)
    font_sub = _load_font(14)
    font_number = _load_font(30)
    font_desc = _load_font(17)
    font_meta = _load_font(14)
    font_badge = _load_font(12)
    font_tiny = _load_font(12)

    pad_x = 22
    header_h = 88
    number_w = font_number.getlength("99999") + 14
    time_w = font_meta.getlength("999分钟前")
    content_x = pad_x + number_w + 16
    content_w = WIDTH - pad_x - content_x - time_w - 12

    row_heights = _layout(rooms, font_desc, content_w)
    footer_h = 46
    body_h = sum(row_heights)
    total_height = header_h + body_h + footer_h + 16
    img = Image.new("RGB", (WIDTH, total_height), BG_COLOR)
    draw = ImageDraw.Draw(img)
    renderer = _TextRenderer(img, draw)

    # 卡片外框 + 表头浅灰底,内容区与页脚用斑马纹区分
    draw.rounded_rectangle(
        [(4, 4), (WIDTH - 5, total_height - 5)], radius=14,
        outline="#E5E5EA", width=1,
    )
    draw.rounded_rectangle(
        [(5, 5), (WIDTH - 6, 5 + header_h)], radius=14, fill="#FAFAFC",
    )
    draw.rectangle([(5, 5 + header_h - 14), (WIDTH - 6, 5 + header_h)], fill="#FAFAFC")

    # 表头:左侧竖条 + 标题 + 副标题,底部一条细分隔线
    draw.rounded_rectangle(
        [(pad_x, 24), (pad_x + 4, 48)], radius=2, fill=ACCENT_COLOR,
    )
    renderer.text((pad_x + 14, 20), title, TEXT_PRIMARY, font_title)
    now_str = datetime.now().strftime("%H:%M:%S")
    renderer.text(
        (pad_x + 14, 56),
        f"{len(rooms)} 个房间 · 更新于 {now_str}",
        TEXT_SECONDARY, font_sub,
    )
    draw.line([(pad_x, 80), (WIDTH - pad_x, 80)], fill=LINE_COLOR, width=1)

    y = header_h + 5
    for i, room in enumerate(rooms):
        # 斑马纹:奇数行铺一层极浅的灰,帮助横向阅读
        if i % 2 == 1:
            draw.rectangle(
                [(6, y), (WIDTH - 7, y + row_heights[i])], fill="#FAFAFC",
            )

        # 房号:胶囊底色 + 主色数字,与首行内容垂直居中
        num_h = font_number.size + 12
        num_y = y + 12
        draw.rounded_rectangle(
            [(pad_x, num_y), (pad_x + number_w, num_y + num_h)],
            radius=8, fill="#EAF2FF",
        )
        num_w = font_number.getlength(room.number)
        renderer.text(
            (pad_x + (number_w - num_w) / 2, num_y + 6),
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
            desc_y += 25

        # 时间戳与房号胶囊垂直居中对齐
        renderer.text(
            (WIDTH - pad_x - time_w, num_y + (num_h - font_meta.size) / 2),
            room.time, TEXT_SECONDARY, font_meta,
        )

        y += row_heights[i]
        if i < len(rooms) - 1:
            draw.line([(pad_x, y), (WIDTH - pad_x, y)], fill=LINE_COLOR, width=1)

    draw.line([(pad_x, y + 6), (WIDTH - pad_x, y + 6)], fill=LINE_COLOR, width=1)
    renderer.text((pad_x, y + 14), REPO_URL, TEXT_TERTIARY, font_tiny)
    renderer.text((pad_x, y + 30), source_url, TEXT_TERTIARY, font_tiny)

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(tmp.name, "PNG")
    tmp.close()
    return tmp.name
