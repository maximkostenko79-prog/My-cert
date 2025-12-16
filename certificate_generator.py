import os
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.lib.colors import Color
import io

def generate_certificate(full_name: str, cert_number: str) -> bytes:
    """
    Генерирует PDF-сертификат, подставляя:
    - Имя и фамилию в дательном падеже под "Выдан:"
    - Номер сертификата под "Номер:"
    Сумма уже есть в шаблоне — не добавляем.
    """
    buffer = io.BytesIO()
    width, height = A4  # 595 x 842 pt

    c = canvas.Canvas(buffer, pagesize=A4)

    # Загружаем фон из шаблона
    template_path = "sertif.png"
    if os.path.exists(template_path):
        img = ImageReader(template_path)
        img_width, img_height = img.getSize()

        # Сохраняем пропорции фона
        aspect_ratio = img_width / img_height
        target_width = width
        target_height = target_width / aspect_ratio

        if target_height > height:
            target_height = height
            target_width = target_height * aspect_ratio

        y_offset = (height - target_height) / 2
        c.drawImage(img, 0, y_offset, width=target_width, height=target_height)
    else:
        # Резервный фон
        c.setFillColor(Color(0.1, 0.1, 0.3))
        c.rect(0, 0, width, height, fill=1)

    # Белый цвет текста
    c.setFillColor(Color(1, 1, 1))
    c.setFont("Helvetica", 18)

    # Простое склонение в дательный падеж
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

    # Позиции (в pt) — подстроены под ваш шаблон
    # Ширина страницы: 595 pt
    # Высота страницы: 842 pt
    # Под «Выдан:» → примерно 500 pt от верха
    name_y = 500  # ← снизу — выше значение, вверх — ниже
    number_y = 300  # ← для «Номер:»

    # Горизонтальное выравнивание — по центру
    c.drawCentredString(width / 2, name_y, dative)
    c.drawCentredString(width / 2, number_y, f"№ {cert_number}")

    c.save()
    buffer.seek(0)
    return buffer.read()
