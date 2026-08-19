from __future__ import annotations

from pathlib import Path

from PIL import ImageFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


def bundled_font() -> Path:
    root = Path(__file__).resolve().parents[3]
    candidates = (
        root / "data" / "external" / "open_handwriting_fonts" / "Yozai-Regular.ttf",
        Path(r"C:\Windows\Fonts\simhei.ttf"),
    )
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError("no bundled Chinese font is available")


def pil_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(bundled_font()), size=size)


def reportlab_font() -> str:
    name = "HomeworkJudgeChinese"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, str(bundled_font())))
    return name
