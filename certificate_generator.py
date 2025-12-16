import os
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import Color
import io

# Регистрируем шрифт с поддержкой кириллицы
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.fonts import addMapping

# DejaVuSans — встроен в reportlab и поддерживает кириллицу
pdfmetrics.registerFont(TTFont('DejaVuSans', 'DejaVuSans.ttf'))

def generate_certificate(full_name: str, cert_number: str) -> bytes:
    """
    Подставляет:
    - Имя и фамилию в дательном падеже (позиция y=880)
    - Номер сертификата (позиция y=426)
    """
    buffer = io.BytesIO()
    width, height = A4  # 595 x 842 pt

    c = canvas.Canvas(buffer, pagesize=A4)

    # Фон
    template_path = "sertif.png"
    if os.path.exists(template_path):
        img = ImageReader(template_path)
        img_width, img_height = img.getSize()

        aspect_ratio = img_width / img_height
        target_width = width
        target_height = target_width / aspect_ratio

        if target_height > height:
            target_height = height
            target_width = target_height * aspect_ratio

        y_offset = (height - target_height) / 2
        c.drawImage(img, 0, y_offset, width=target_width, height=target_height)
    else:
        c.setFillColor(Color(0.1, 0.1, 0.3))
        c.rect(0, 0, width, height, fill=1)

    # Белый текст
    c.setFillColor(Color(1, 1, 1))

    # Склонение в дательный падеж
    parts = full_name.strip().split()
    if len(parts) >= 2:
        first, last = parts[0], parts[1]
        if first.endswith('й'):
            first_d = first[:-1] + 'ю'
        elif first.endswith('ь'):
            first_d = first[:-1] + 'ю'
        else:
            first_d = first + 'у'
        if last.endswith('в'):
            last_d = last + 'у'
        else:
            last_d = last + 'у'
        dative = f"{first_d} {last_d}"
    else:
        dative = full_name

    # Шрифт с поддержкой кириллицы
    c.setFont("DejaVuSans", 20)
    c.drawCentredString(width / 2, 434, dative)

    c.setFont("DejaVuSans", 18)
    c.drawCentredString(width / 2, 571, f"№ {cert_number}")

    c.save()
    buffer.seek(0)
    return buffer.read()
