import os
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import Color
import io

# Регистрируем шрифт (используем встроенный Helvetica, чтобы не зависеть от внешних файлов)
# Если захочешь свой шрифт — можно добавить позже

def generate_certificate(full_name: str, amount: int, cert_number: str) -> bytes:
    """
    Генерирует PDF-сертификат и возвращает его как байты.
    """
    buffer = io.BytesIO()
    width, height = A4  # 595 x 842 pt

    c = canvas.Canvas(buffer, pagesize=A4)

    # Путь к шаблону — он будет лежать рядом с кодом
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

    # Форматируем сумму красиво
    amount_str = f"{amount:,}".replace(",", " ") + " ₽"

    # Настраиваем шрифт
    c.setFont("Helvetica", 18)

    # Позиции (в pt). Подстрой при необходимости.
    name_y = height - 200
    amount_y = height - 240
    number_y = height - 280

    c.drawCentredString(width / 2, name_y, f"Выдан(а): {full_name}")
    c.drawCentredString(width / 2, amount_y, f"Сумма: {amount_str}")
    c.drawCentredString(width / 2, number_y, f"Номер: {cert_number}")

    c.save()
    buffer.seek(0)
    return buffer.read()
