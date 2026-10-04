"""把房间列表画成一张图片。

同步函数,调用方应放进 asyncio.to_thread,避免阻塞 bot 事件循环。
"""

import tempfile
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

try:
    from pilmoji import Pilmoji
except ImportError:
    Pilmoji = None

from .station_api import RoomInfo

WIDTH = 580
MARGIN = 24
ROW_HEIGHT = 72
LINE_COLOR = "#E5E5EA"
BG_COLOR = "#FFFFFF"
TEXT_PRIMARY = "#1D1D1F"
TEXT_SECONDARY = "#6E6E73"
TEXT_TERTIARY = "#86868B"
ACCENT_COLOR = "#007AFF"

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

_font_cache: dict[int, ImageFont.FreeTypeFont] = {}


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    cached = _font_cache.get(size)
    if cached is not None:
        return cached
    for path in _FALLBACK_FONT_PATHS:
        try:
            font = ImageFont.truetype(path, size)
            _font_cache[size] = font
            return font
        except Exception:
            continue
    return ImageFont.load_default()


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


def _layout(rooms: list[RoomInfo], font_desc: ImageFont.FreeTypeFont, content_w: int):
    """每行高度:顶部偏移 + 描述行 + 底部间距,不低于最小行高。"""
    row_heights = []
    for room in rooms:
        desc_lines = _wrap_text(room.raw, font_desc, content_w)
        needed = 34 + len(desc_lines) * 24 + 12
        row_heights.append(max(ROW_HEIGHT, needed))
    return row_heights


class _Canvas:
    """统一 pilmoji 与纯 PIL 的绘制接口。

    pilmoji 默认从 emojicdn.elk.sh 拉 emoji 贴图且没有缓存,国内网络经常失败,
    所以任何一次绘制异常都会降级为纯 PIL(emoji 显示为缺字方块,但图能出)。
    """

    def __init__(self, img: Image.Image):
        self.img = img
        self._pilmoji = None
        self._draw = None
        if Pilmoji is not None:
            try:
                self._pilmoji = Pilmoji(img).__enter__()
            except Exception:
                self._pilmoji = None
        if self._pilmoji is None:
            self._draw = ImageDraw.Draw(img)

    def text(self, xy, text, fill, font) -> None:
        if self._pilmoji is not None:
            try:
                self._pilmoji.text(xy, text, fill, font)
                return
            except Exception:
                self.close()
                self._draw = ImageDraw.Draw(self.img)
        self._draw.text(xy, text, font=font, fill=fill)

    def close(self) -> None:
        if self._pilmoji is not None:
            try:
                self._pilmoji.__exit__(None, None, None)
            except Exception:
                pass
            self._pilmoji = None


REPO_URL = "https://github.com/yonglanws/astrbot_plugin_pjsk_station"


def render_rooms(rooms: list[RoomInfo], title: str, source_url: str) -> str:
    """渲染房间列表,返回临时 PNG 文件路径(调用方负责删除)。"""
    font_title = _load_font(32)
    font_sub = _load_font(16)
    font_number = _load_font(34)
    font_desc = _load_font(18)
    font_meta = _load_font(15)
    font_tiny = _load_font(13)

    content_x = 145
    content_w = WIDTH - MARGIN - content_x - 100
    header_h = 100

    row_heights = _layout(rooms, font_desc, content_w)
    total_height = header_h + sum(row_heights) + 56
    img = Image.new("RGB", (WIDTH, total_height), BG_COLOR)
    canvas = _Canvas(img)

    try:
        canvas.text((MARGIN, 20), title, TEXT_PRIMARY, font_title)
        now_str = datetime.now().strftime("%H:%M:%S")
        canvas.text(
            (MARGIN, 62),
            f"{len(rooms)} 个房间 · 更新于 {now_str}",
            TEXT_SECONDARY, font_sub,
        )

        y = header_h
        for i, room in enumerate(rooms):
            canvas.text((MARGIN, y + 14), room.number, ACCENT_COLOR, font_number)

            if room.username:
                canvas.text((content_x, y + 10), room.username, TEXT_SECONDARY, font_meta)

            desc_y = y + 34
            for line in _wrap_text(room.raw, font_desc, content_w):
                canvas.text((content_x, desc_y), line, TEXT_PRIMARY, font_desc)
                desc_y += 24

            time_w = font_meta.getlength(room.time)
            canvas.text((WIDTH - MARGIN - time_w, y + 26), room.time, TEXT_SECONDARY, font_meta)

            y += row_heights[i]

        canvas.text((MARGIN, y + 12), REPO_URL, TEXT_TERTIARY, font_tiny)
        canvas.text((MARGIN, y + 32), source_url, TEXT_TERTIARY, font_tiny)
    finally:
        canvas.close()

    draw = ImageDraw.Draw(img)
    y = header_h
    for i in range(len(rooms)):
        line_y = y + row_heights[i] - 1
        draw.line([(MARGIN, line_y), (WIDTH - MARGIN, line_y)], fill=LINE_COLOR, width=1)
        y += row_heights[i]

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(tmp.name, "PNG")
    tmp.close()
    return tmp.name
