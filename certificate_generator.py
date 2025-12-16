import os
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import Color
import io

def generate_certificate(full_name: str, cert_number: str) -> bytes:
    """
    Генерирует PDF-сертификат с именем в дательном падеже, номером и фиксированной суммой.
    """
    buffer = io.BytesIO()
    width, height = A4  # 595 x 842 pt

    c = canvas.Canvas(buffer, pagesize=A4)

    # Путь к шаблону
    template_path = "sertif.png"
    if os.path.exists(template_path):
        c.drawImage(ImageReader(template_path), 0, 0, width=width, height=height)
    else:
        # Если шаблон не найден — рисуем простой фон
        c.setFillColor(Color(0.95, 0.95, 0.95))
        c.rect(0, 0, width, height, fill=1)
        c.setFillColor(Color(0, 0, 0))
        c.setFont("Helvetica-Bold", 24)
        c.drawCentredString(width / 2, height - 100, "ПОДАРОЧНЫЙ СЕРТИФИКАТ")

    # Форматируем имя в дательном падеже (простая замена, можно улучшить)
    # Это пример — для реального проекта используйте библиотеку pymorphy2
    dative_name = full_name  # временно оставим как есть
    # Пример: если имя "Василий Федоров" → "Василию Федорову"
    # Можно сделать так:
    parts = full_name.split()
    if len(parts) >= 2:
        first_name = parts[0]
        last_name = parts[1]
        # Простая замена окончаний (не универсально, но работает для многих имён)
        if first_name.endswith('й'):
            first_name_dative = first_name[:-1] + 'ю'
        elif first_name.endswith('ь'):
            first_name_dative = first_name[:-1] + 'ю'
        else:
            first_name_dative = first_name + 'у'
        last_name_dative = last_name + 'у' if last_name.endswith('в') else last_name + 'у'
        dative_name = f"{first_name_dative} {last_name_dative}"

    # Позиции (в pt). Подстрой при необходимости.
    name_y = height - 200
    amount_y = height - 240
    number_y = height - 280

    c.setFont("Helvetica", 18)
    c.drawCentredString(width / 2, name_y, f"Выдан: {dative_name}")
    c.drawCentredString(width / 2, amount_y, "Сумма: 2 000 руб.")
    c.drawCentredString(width / 2, number_y, f"Номер: {cert_number}")

    c.save()
    buffer.seek(0)
    return buffer.read()
