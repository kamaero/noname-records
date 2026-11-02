"""Шаблон сессии Audition: цепочки эффектов по ролям — то, что владелец вешает руками.

Пресет дорожки в .sesx не ссылкой, а развёрнутой цепочкой (`<effectsRack>`) со всеми
параметрами каждого эффекта: сами пресеты («Япндесфярюк» и другие) живут в настройках
Audition на машине владельца, и сервер до них не дотянется. Поэтому владелец один раз
собирает шаблон — дорожка на роль, названа как роль, на ней её пресет — и присылает его
боту. Сборка главы копирует цепочку на дорожку с тем же именем.

Обязательный первый эффект каждой голосовой дорожки — Single-band Compressor с пресетом
Voice Over (владелец, 25.10): роль без своей цепочки получает его одного, а цепочка, где
его нет первым, — его первым.

Шаблон читается срезами строк, а не XML-разбором: `presetChunkData` и `busConfig` лежат
в CDATA, и переразбор с пересборкой мог бы их переписать. Срез переносит их байт в байт.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from app.paths import data_path
from app.services.author_profile import normalize_name

TEMPLATE_PATH = str(data_path("session_template.sesx"))
#: шаблон без клипов весит сотни килобайт; больше — это не шаблон, а чья-то глава
MAX_TEMPLATE_BYTES = 20 * 1024 * 1024
#: Single-band Compressor — так его зовёт Audition в componentID; имя может быть переведено
VOICE_OVER_COMPONENT = "2da3e2bc-5005-481c-bbeb-7689d3169edf"
VOICE_OVER_PRESET = "Voice Over"
#: универсальная дорожка владельца — для персонажа, у которого нет своего пресета
ANY_VOICE = "Любой голос"

_TRACK_RE = re.compile(r"<audioTrack\b.*?</audioTrack>", re.S)
_NAME_RE = re.compile(r"<trackParameters\b[^>]*>\s*<name>([^<]*)</name>")
_RACK_RE = re.compile(r"(<effectsRack\b[^>]*>)(.*?)(</effectsRack>)", re.S)
_SLOT_RE = re.compile(r"<slot\b.*?</slot>", re.S)
_CHANNEL_RE = re.compile(r'<trackAudioParameters\b[^>]*\baudioChannelType="([^"]*)"')
_GUID_RE = re.compile(r'componentGuid="[^"]*"')
_SLOT_INDEX_RE = re.compile(r'(<slot\b[^>]*\bslotIndex=")\d+(")')


def role_key(name: str) -> str:
    """Ключ сверки дорожки с ролью: только буквы и цифры. «ГеКоон» в шаблоне — это
    «Ге'Коон» в касте (владелец 25.10: апостроф в Audition ставить незачем)."""
    return "".join(ch for ch in normalize_name(str(name or "")) if ch.isalnum())


class TemplateError(ValueError):
    """Файл не годится в шаблон. Текст — для владельца в боте."""


@dataclass(frozen=True)
class TrackFx:
    """Что положить на дорожку: цепочку эффектов целиком и тип канала её дорожки."""

    rack: str
    channel_type: str


@dataclass
class _Rack:
    open_tag: str
    header: str
    slots: list[str]
    close_tag: str
    channel_type: str


@dataclass
class Template:
    #: по нормализованному имени дорожки
    racks: dict[str, _Rack] = field(default_factory=dict)
    #: имена дорожек, как их написал владелец, — для ответа в боте
    roles: list[str] = field(default_factory=list)
    #: слот Voice Over и цепочка, откуда он взят (её обвязка — для роли без своей)
    voice_over: str = ""
    voice_over_rack: _Rack | None = None
    #: цепочка «Любого голоса» — для ролей без своей
    any_voice: _Rack | None = None


def _is_voice_over(slot: str) -> bool:
    preset = f'presetName="{VOICE_OVER_PRESET}"' in slot
    return preset and (f'componentID="{VOICE_OVER_COMPONENT}"' in slot or 'name="Single-band Compressor"' in slot)


def _is_compressor(slot: str) -> bool:
    return f'componentID="{VOICE_OVER_COMPONENT}"' in slot or 'name="Single-band Compressor"' in slot


def parse_template(xml: str) -> Template:
    if "<sesx" not in xml or "<audioTrack" not in xml:
        raise TemplateError("Это не сессия Audition (.sesx) — в файле нет дорожек.")
    if "<!ENTITY" in xml:
        # Audition сущностей не пишет; файл с ними — не его
        raise TemplateError("В файле есть объявления XML-сущностей — это не сессия Audition.")
    template = Template()
    for block in _TRACK_RE.findall(xml):
        name_match, rack_match = _NAME_RE.search(block), _RACK_RE.search(block)
        if not name_match or not rack_match:
            continue
        name = name_match.group(1).strip()
        body = rack_match.group(2)
        slots = _SLOT_RE.findall(body)
        if not name or not slots:
            continue
        first = _SLOT_RE.search(body)
        channel = _CHANNEL_RE.search(block)
        rack = _Rack(open_tag=rack_match.group(1), header=body[:first.start()] if first else body, slots=slots,
                     close_tag=rack_match.group(3), channel_type=channel.group(1) if channel else "stereo")
        key = role_key(name)
        if key == role_key(ANY_VOICE):
            template.any_voice = template.any_voice or rack
        elif key in template.racks:
            continue  # первая дорожка с этим именем — та, что выше в сессии
        else:
            template.racks[key] = rack
            template.roles.append(name)
        if not template.voice_over:
            found = next((slot for slot in slots if _is_voice_over(slot)), "")
            if found:
                template.voice_over, template.voice_over_rack = found, rack
    if not template.racks and template.any_voice is None:
        raise TemplateError("В сессии нет дорожек с эффектами — повесьте пресеты на дорожки ролей.")
    return template


def _assemble(rack: _Rack, slots: list[str]) -> str:
    renumbered = [_SLOT_INDEX_RE.sub(lambda m, i=i: f"{m.group(1)}{i}{m.group(2)}", slot, count=1)
                  for i, slot in enumerate(slots)]
    text = rack.open_tag + rack.header + "".join(renumbered) + rack.close_tag
    # свой GUID каждому экземпляру: Audition не терпит повторов внутри файла
    return _GUID_RE.sub(lambda _m: f'componentGuid="{uuid4()}"', text)


def rack_for(template: Template, role: str) -> TrackFx | None:
    """Цепочка для голосовой дорожки роли — с Voice Over первым. `None` — класть нечего."""
    own = template.racks.get(role_key(role)) or template.any_voice
    if own is None:
        if not template.voice_over or template.voice_over_rack is None:
            return None
        base = template.voice_over_rack
        return TrackFx(rack=_assemble(base, [template.voice_over]), channel_type=base.channel_type)
    slots = list(own.slots)
    # Подстроенный под голос Voice Over теряет имя пресета («(Custom)»), но остаётся
    # тем же компрессором — он и есть Voice Over роли. Не узнав его, мы вешали рядом
    # второй, стандартный (25.10, четыре дорожки по два компрессора).
    current = (next((slot for slot in slots if _is_voice_over(slot)), "")
               or next((slot for slot in slots if _is_compressor(slot)), ""))
    if current:
        slots.remove(current)
        slots.insert(0, current)
    elif template.voice_over:
        slots.insert(0, template.voice_over)
    return TrackFx(rack=_assemble(own, slots), channel_type=own.channel_type)


def unknown_roles(template: Template, known: list[str]) -> list[str]:
    """Дорожки шаблона, которым нет роли ни в одной книге: опечатка или роль будущей книги."""
    keys = {role_key(name) for name in known}
    return [name for name in template.roles if role_key(name) not in keys]


def apply_template(tracks, template: Template | None, *, voices: set[str]) -> None:
    """Повесить цепочки на голосовые дорожки. Эмбиент, саундтрек и шумы — не голос."""
    if template is None:
        return
    for track in tracks:
        if track.name not in voices:
            continue
        fx = rack_for(template, track.name)
        if fx is not None:
            track.effects_rack, track.channel_type = fx.rack, fx.channel_type


def save_template(data: bytes, *, path: str | None = None) -> Template:
    """Проверить и положить шаблон. Негодный файл прежний не трогает."""
    path = path or TEMPLATE_PATH
    if len(data) > MAX_TEMPLATE_BYTES:
        raise TemplateError("Файл слишком большой для шаблона — сохраните сессию без клипов.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TemplateError("Это не сессия Audition (.sesx) — файл не текстовый.") from exc
    template = parse_template(text)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)
    return template


def load_template(*, path: str | None = None) -> Template | None:
    """Шаблон с диска; нет файла или он испорчен — без эффектов, как было до шаблонов."""
    path = path or TEMPLATE_PATH
    try:
        with open(path, encoding="utf-8-sig") as handle:
            return parse_template(handle.read())
    except (OSError, TemplateError):
        return None


# --- приём шаблона ботом -------------------------------------------------------------

def incoming_template(message: dict) -> dict | None:
    """Документ .sesx в сообщении — `{"file_id", "file_size"}`, иначе `None`."""
    doc = message.get("document")
    if not isinstance(doc, dict) or not str(doc.get("file_name") or "").lower().endswith(".sesx"):
        return None
    return {"file_id": str(doc.get("file_id") or ""), "file_size": int(doc.get("file_size") or 0)}


def receive(chat_id, doc: dict) -> list:
    """Ответ владельцу сразу; скачивание и проверка — в воркере, как у демо."""
    from app.services.bot_out import Outgoing, send

    if doc["file_size"] > MAX_TEMPLATE_BYTES:
        return [send(chat_id, "Файл слишком большой для шаблона — сохраните сессию без клипов и пришлите снова.")]
    return [send(chat_id, "Шаблон сессии получил, проверяю."),
            Outgoing("enqueue_session_template", {"file_id": doc["file_id"], "chat_id": chat_id})]


def enqueue_store_template(file_id: str, chat_id) -> str | None:
    from app.db import SessionLocal
    from app.models import BackgroundRun
    from app.workers.launcher import enqueue_tracked_task

    return enqueue_tracked_task(
        "high", "app.worker_tasks.perform_session_template_task", SessionLocal, BackgroundRun,
        job_kind="session_template", entity_type="session_template", entity_id=str(file_id)[:36],
        run_key=f"session_template:{str(file_id)[:60]}", file_id=str(file_id), chat_id=str(chat_id),
    )


def _summary(template: Template, known_roles: list[str] | None = None) -> str:
    roles = ", ".join(template.roles)
    lines = [f"Шаблон сессии принят. Пресеты для ролей ({len(template.roles)}): {roles}."]
    if template.any_voice is not None:
        lines.append(f"«{ANY_VOICE}» — для ролей без своего пресета.")
    if template.voice_over:
        lines.append("Voice Over найден: он встанет первым на каждую голосовую дорожку"
                     + ("." if template.any_voice is not None else ", а роли без своего пресета получат только его."))
    else:
        lines.append("Voice Over (Single-band Compressor) в шаблоне не нашёл — первым его не поставлю.")
    missing = unknown_roles(template, known_roles) if known_roles else []
    if missing:
        lines.append(f"Таких ролей нет ни в одной книге: {', '.join(missing)} — проверьте написание "
                     "или ждите книгу, где они есть.")
    lines.append("Пресеты будут в каждом архиве главы, скачанном с этой минуты.")
    return "\n".join(lines)


def store_from_telegram(file_id: str, chat_id, *, fetch, send_text, known_roles: list[str] | None = None) -> str:
    """Скачать, проверить, положить и ответить владельцу. Итог — строка для журнала прогона."""
    try:
        with fetch(file_id) as handle:
            data = handle.read(MAX_TEMPLATE_BYTES + 1)
    except Exception:  # noqa: BLE001 — владелец должен узнать, что файл не дошёл
        send_text(chat_id, "Не получилось скачать шаблон — пришлите его ещё раз.")
        return "не скачан"
    try:
        template = save_template(data)
    except TemplateError as exc:
        send_text(chat_id, f"Шаблон не принят: {exc} Прежний шаблон остался.")
        return "отклонён"
    send_text(chat_id, _summary(template, known_roles))
    return f"ролей {len(template.roles)}"
