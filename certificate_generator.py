# certificate_generator.py
import os
from PIL import Image, ImageDraw, ImageFont
import io

def generate_certificate_image(full_name: str, cert_number: str) -> bytes:
    template_path = "sertif.png"
    if not os.path.exists(template_path):
        raise FileNotFoundError("Файл шаблона 'sertif.png' не найден")

    base = Image.open(template_path).convert("RGBA")
    width, height = base.size

    txt_layer = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(txt_layer)

    # Пытаемся использовать системный шрифт с кириллицей
    try:
        # DejaVuSans часто доступен в Linux-образах
        font_large = ImageFont.truetype("DejaVuSans.ttf", 40)
        font_small = ImageFont.truetype("DejaVuSans.ttf", 30)
    except OSError:
        try:
            # Альтернатива: LiberationSans
            font_large = ImageFont.truetype("LiberationSans-Regular.ttf", 40)
            font_small = ImageFont.truetype("LiberationSans-Regular.ttf", 30)
        except OSError:
            # Fallback: без файла (может не поддерживать кириллицу)
            font_large = ImageFont.load_default()
            font_small = ImageFont.load_default()

    text_color = (255, 255, 255, 255)  # белый, непрозрачный

    # Позиции в пикселях (точно как в шаблоне)
    name_pos = (width // 2, 422)
    number_pos = (width // 2, 704)

    draw.text(name_pos, full_name.strip(), fill=text_color, font=font_large, anchor="mm")
    draw.text(number_pos, f"№ {cert_number}", fill=text_color, font=font_small, anchor="mm")

    result = Image.alpha_composite(base, txt_layer)
    buffer = io.BytesIO()
    result.convert("RGB").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer.read()
