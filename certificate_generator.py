import os
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.lib.colors import Color
import io

def generate_certificate(full_name: str, cert_number: str) -> bytes:
    """
    Генерирует PDF-сертификат, подставляя только:
    - Имя и фамилию в дательном падеже
    - Номер сертификата
    Сумма уже есть в шаблоне sertif.png — не дублируем.
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
        # Резервный фон (на случай, если файл отсутствует)
        c.setFillColor(Color(0.1, 0.1, 0.3))
        c.rect(0, 0, width, height, fill=1)

    # Белый текст (под тёмный фон)
    c.setFillColor(Color(1, 1, 1))
    c.setFont("Helvetica", 18)

    # Простое склонение в дательный падеж
    parts = full_name.strip().split()
    if len(parts) >= 2:
        first, last = parts[0], parts[1]
        # Имя
        if first.endswith('й'):
            first_d = first[:-1] + 'ю'
        elif first.endswith('ь'):
            first_d = first[:-1] + 'ю'
        else:
            first_d = first + 'у'
        # Фамилия
        if last.endswith('в'):
            last_d = last + 'у'
        else:
            last_d = last + 'у'  # упрощённо
        dative = f"{first_d} {last_d}"
    else:
        dative = full_name

    # Позиции — подстрой под свой шаблон!
    name_y = height - 220  # ← измени, если текст не там, где нужно
    number_y = height - 260

    c.drawCentredString(width / 2, name_y, dative)
    c.drawCentredString(width / 2, number_y, f"№ {cert_number}")

    c.save()
    buffer.seek(0)
    return buffer.read()
