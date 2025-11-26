# app/vision_client.py
from __future__ import annotations

import io
from typing import Literal

from PIL import Image
import pytesseract

SupportedLang = Literal["es", "en"]


def analyze_image_bytes(image_bytes: bytes, lang: SupportedLang = "es") -> str:
    """
    Análisis local sin API key.

    - Usa Tesseract para extraer texto (OCR) de la imagen.
    - Devuelve un texto descriptivo que luego el LLM puede usar como contexto.
    - No llama a ningún servicio externo.
    """
    # 1) Abrir imagen
    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception as exc:
        return (
            "⚠️ No pude abrir la imagen (formato no válido o archivo dañado). "
            f"Detalle interno: {exc}"
        )

    # 2) Idiomas para OCR (es + en para capturar la mayoría de mensajes)
    ocr_lang = "spa+eng"

    try:
        raw_text = pytesseract.image_to_string(img, lang=ocr_lang)
    except Exception as exc:
        return (
            "⚠️ Fallo al intentar leer el texto de la imagen con OCR. "
            f"Detalle interno: {exc}"
        )

    raw_text = (raw_text or "").strip()
    if not raw_text:
        # No se encontró texto legible
        if lang == "es":
            return (
                "Analicé la imagen con OCR, pero no pude encontrar texto legible.\n\n"
                "Si es un error del sistema o una notificación, intenta hacer un "
                "zoom o una captura más cercana al mensaje, o describe el problema "
                "con tus propias palabras."
            )
        else:
            return (
                "I ran OCR on the image but couldn't find any readable text.\n\n"
                "If this is a system error or notification, try a closer screenshot "
                "or describe the issue in your own words."
            )

    # 3) Construir respuesta amigable para el LLM
    if lang == "es":
        return (
            "He extraído el siguiente texto de la imagen (usando OCR local, "
            "sin servicios externos):\n\n"
            f"```text\n{raw_text}\n```\n\n"
            "Puedes pedirme que te explique este mensaje, que te ayude a "
            "solucionar el error, o que lo reescriba de forma más clara."
        )
    else:
        return (
            "I extracted the following text from the image (using local OCR, "
            "no external services):\n\n"
            f"```text\n{raw_text}\n```\n\n"
            "You can ask me to explain this message, help troubleshoot the error, "
            "or rewrite it more clearly."
        )
