import asyncio
from io import BytesIO
from pathlib import Path
import os

from PIL import Image, ImageDraw, ImageFont
from webassistant.ocr import LocalOCR


def test_local_model_inference_on_generated_text():
    # A generated fixture, not a real website challenge.
    image = Image.new("RGB", (150, 55), "white")
    font = ImageFont.truetype(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/arial.ttf"), 36)
    ImageDraw.Draw(image).text((12, 5), "1234", font=font, fill="black")
    data = BytesIO()
    image.save(data, "PNG")
    ocr = LocalOCR()
    try:
        result = asyncio.run(ocr.recognize(data.getvalue()))
        assert result == "1234"
    finally:
        ocr.close()
