"""Как студия называет себя в письмах и куда ведут ссылки.

Система ставится разными студиями; имя, адрес сайта и «кому писать» берутся из настроек,
а не вшиваются в тексты. Иначе каждая установка рассылала бы дикторам чужое имя и ссылку
на чужой сайт.
"""
from __future__ import annotations

from app.config import settings


def name() -> str:
    return (settings.studio_name or "").strip() or "Noname Records"


def write_to() -> str:
    """«напишите Ольге» — STUDIO_CONTACT_NAME задаётся в дательном падеже; пусто — в студию."""
    contact = (settings.studio_contact_name or "").strip()
    return f"напишите {contact}" if contact else "напишите в студию"


def site_url(path: str = "") -> str:
    base = (settings.app_base_url or "").strip().rstrip("/")
    return f"{base}{path}" if base else path


def site_host() -> str:
    """Адрес сайта без схемы — так его удобнее читать в письме: «example.com → Войти»."""
    base = (settings.app_base_url or "").strip().rstrip("/")
    return base.split("://", 1)[-1] if base else ""
