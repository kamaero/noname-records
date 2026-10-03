from __future__ import annotations

import re
from typing import Any, Callable

from fastapi import Request, status
from fastapi.responses import JSONResponse
from app.api._helpers import bad_request_response, forbidden_response, not_found_response, unauthorized_response
from pydantic import BaseModel
from app.services.budget_rows import collapse_budget_character_rows
from app.services.studio_settings import (
    default_rate_rub_per_min,
    set_default_rate,
    set_usd_rub_rate,
    studio_settings,
    usd_rub_rate,
)
from app.services.role_approval import notify_role_approved, should_notify_actor_change
from app.services.casting import propagate_approval, rebuild_assignments
from app.services.role_votes import cast_role_vote, vote_weight
from app.services.shared_runtime import NARRATOR_DISPLAY_NAME, is_narrator_name
from app.time_utils import utcnow_naive
from app.v2.budget_ops import rebuild_v2_budget, v2_budget_is_stale
from app.v2.cast_ops import as_tentative


class SaveBookBudgetPayload(BaseModel):
    narrative_cost_rub: int = 0
    sound_engineer_cost_rub: int = 0
    extra_cost_rub: int = 0
    notes: str = ""
    narrator_actor_name: str = ""


class SaveBudgetCharacterPayload(BaseModel):
    name: str | None = None
    appears_in: str | None = None
    actor_name: str | None = None
    race: str | None = None
    temperament: str | None = None
    lines_count: int | None = None
    duration_seconds: int | None = None
    manual_rate_rub_per_min: int | None = None
    manual_fixed_rub: int | None = None
    character_color: str | None = None
    character_text_color: str | None = None
    character_font_weight: str | None = None
    character_font_style: str | None = None


class SaveBookCardPayload(BaseModel):
    book_annotation: str = ""


class SaveStudioRatePayload(BaseModel):
    default_rate_rub_per_min: int = 1000
    #: rubles per dollar; omitted leaves the stored one alone
    usd_rub_rate: float | None = None


def build_budget_api_handlers(deps: dict[str, Any]) -> dict[str, Callable[..., Any]]:
    is_authenticated = deps["is_authenticated"]
    has_workspace_full_access = deps["has_workspace_full_access"]
    has_any_role = deps["has_any_role"]
    # Через `deps`, а не прямым импортом из `app.auth`: контрактный часовой
    # (`tests/test_handler_dependency_contracts.py`) читает исходник и видит только
    # обращения к `deps` — прямой импорт для него невидим, и фейковый словарь в тестах
    # про такую зависимость не узнает. Тот же короткий путь в `recording.py` пришлось
    # откатывать по этой самой причине.
    is_agent = deps["is_agent"]
    session_payload = deps["session_payload"]
    session_roles = deps["session_roles"]
    session_display_name = deps["session_display_name"]
    session_local = deps["SessionLocal"]
    get_book = deps["get_book"]
    build_character_style_maps = deps["build_character_style_maps"]
    format_dt = deps["format_dt"]
    book_budget_model = deps["BookBudget"]
    character_model = deps["Character"]
    character_budget_snapshot_model = deps["CharacterBudgetSnapshot"]
    script_chapter_model = deps["ScriptChapter"]

    def parse_appears_in(raw_value: str) -> list[int]:
        return sorted({int(part) for part in str(raw_value or "").replace(";", ",").split(",") if part.strip().isdigit()})

    def normalize_appears_in(raw_value: str) -> str:
        return ",".join(str(item) for item in parse_appears_in(raw_value))

    def normalize_hex_color(raw_value: str | None, fallback: str = "") -> str:
        value = str(raw_value or "").strip()
        if not value:
            return fallback
        if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            return value.upper()
        if re.fullmatch(r"[0-9a-fA-F]{6}", value):
            return f"#{value.upper()}"
        return fallback

    def calc_snapshot_total(approx_seconds: int, manual_rate_rub_per_min: int | None, manual_fixed_rub: int | None, current_total: int, fallback_rate: int = 1000) -> int:
        if int(manual_fixed_rub or 0) > 0:
            return int(manual_fixed_rub or 0)
        if int(manual_rate_rub_per_min or 0) > 0:
            return int(round(int(manual_rate_rub_per_min or 0) * (max(0, int(approx_seconds or 0)) / 60.0)))
        if int(approx_seconds or 0) > 0:
            return int(round(fallback_rate * (max(0, int(approx_seconds or 0)) / 60.0)))
        return int(current_total or 0)

    def unauthorized():
        return unauthorized_response()

    def forbidden():
        return forbidden_response()

    def require_budget_view_access(request: Request):
        if not is_authenticated(request):
            return unauthorized()
        if not (has_workspace_full_access(request) or has_any_role(request, {"author"})):
            return forbidden()
        return None

    def require_admin(request: Request):
        if not is_authenticated(request):
            return unauthorized()
        if not has_any_role(request, {"admin"}):
            return forbidden()
        return None

    def require_budget_write_access(request: Request):
        if not is_authenticated(request):
            return unauthorized()
        if not has_any_role(request, {"admin", "author"}):
            return forbidden()
        return None

    #: Отказ агенту в остальных полях — словами, а не кодом: он увидит его в интерфейсе.
    AGENT_FIELDS_REFUSAL = (
        "Агент вписывает в каст только актёра — предварительно, со знаком вопроса. "
        "Расу, темперамент, описание и деньги правят автор и владелец студии."
    )
    #: Рассказчик — не роль из списка: его выбирает владелец, и запрет живёт здесь, а не
    #: только в интерфейсе. `canAssignActor` прячет поле, но у строки рассказчика в касте
    #: настоящий `character_id`, и адрес ручки обходит любое сокрытие.
    AGENT_NARRATOR_REFUSAL = (
        "Рассказчика выбирает владелец студии: он читает книгу целиком, и это назначение "
        "не бывает предварительным. Агент предлагает актёров на остальные роли."
    )

    def cast_assign_scope(request: Request) -> str:
        """Насколько широко этому человеку открыта карточка персонажа.

        `"all"` — вся карточка, `"actor_only"` — одно поле актёра, `""` — не пускать.

        Агент — кастинг-директор: он предлагает актёра и не трогает ничего другого.
        Отдельная функция, а не третья ветка в гварде, потому что «кто может
        назначать» должно читаться одной строкой.
        """
        if has_any_role(request, {"admin", "author"}):
            return "all"
        if is_agent(request):
            return "actor_only"
        return ""

    def ensure_book_palette(characters) -> bool:
        before = {
            str(getattr(character, "id", "")): (
                str(getattr(character, "character_color", "") or ""),
                str(getattr(character, "character_text_color", "") or ""),
                str(getattr(character, "character_font_weight", "") or ""),
                str(getattr(character, "character_font_style", "") or ""),
            )
            for character in characters
        }
        build_character_style_maps(characters)
        after = {
            str(getattr(character, "id", "")): (
                str(getattr(character, "character_color", "") or ""),
                str(getattr(character, "character_text_color", "") or ""),
                str(getattr(character, "character_font_weight", "") or ""),
                str(getattr(character, "character_font_style", "") or ""),
            )
            for character in characters
        }
        return before != after

    def is_narrator_row(character: dict[str, Any]) -> bool:
        return (
            is_narrator_name(str(character.get("name") or ""))
            or is_narrator_name(str(character.get("aliases") or ""))
            or is_narrator_name(str(character.get("narrator_role") or ""))
        )

    def detect_author_chapter_count(chapters) -> int:
        max_number = 0
        for chapter in chapters:
            title = str(getattr(chapter, "chapter_title", "") or "")
            for match in re.finditer(r"\b(?:глава|chapter)\s+(\d{1,4})\b", title, flags=re.IGNORECASE):
                max_number = max(max_number, int(match.group(1)))
        return max_number or len(chapters)

    def build_character_rows(characters, snapshot_map, *, narrator_fixed_rub: int, narrator_actor_name: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
        rows = []
        character_total = 0
        dialogue_lines = 0
        dialogue_seconds = 0
        cast_only_count = 0
        unpriced_playable_roles = 0
        narrator_source_lines = 0
        narrator_source_seconds = 0
        for character in characters:
            character_id = str(character.get("id") or "")
            snap = snapshot_map.get(character_id)
            source_lines = int((snap.lines_count if snap else 0) or 0)
            source_seconds = int((snap.approx_seconds if snap else 0) or 0)
            narrator = is_narrator_row(character)
            if narrator:
                row_total = int(narrator_fixed_rub or 0)
                narrator_source_lines += source_lines
                narrator_source_seconds += source_seconds
                lines_count = 0
                approx_seconds = source_seconds
                fact_seconds = source_seconds
            else:
                row_total = int((snap.total_rub if snap else 0) or 0)
                character_total += row_total
                dialogue_lines += source_lines
                dialogue_seconds += source_seconds
                if source_lines <= 0:
                    cast_only_count += 1
                if source_lines > 0 and row_total <= 0:
                    unpriced_playable_roles += 1
                lines_count = source_lines
                approx_seconds = source_seconds
                fact_seconds = int((snap.fact_seconds if snap else 0) or 0)
            appears_in_chapters = parse_appears_in(character.get("appears_in") or "")
            rows.append(
                {
                    "character_id": character_id,
                    "char_map_id": str(character.get("char_map_id") or ""),
                    "name": NARRATOR_DISPLAY_NAME if narrator else str(character.get("name") or ""),
                    "is_narrator": narrator,
                    "aliases": str(character.get("aliases") or ""),
                    "race": str(character.get("race") or ""),
                    "temperament": str(character.get("temperament") or ""),
                    "note": str(character.get("note") or ""),
                    "actor_name": narrator_actor_name if narrator and narrator_actor_name else str(character.get("actor_name") or ""),
                    "appears_in_chapters": appears_in_chapters,
                    "chapter_count": len(appears_in_chapters),
                    "lines_count": lines_count,
                    "approx_seconds": approx_seconds,
                    "fact_seconds": fact_seconds,
                    "calc_mode": "fixed" if narrator else ((snap.calc_mode if snap else "") or ""),
                    "manual_rate_rub_per_min": None if narrator else character.get("manual_rate_rub_per_min"),
                    "manual_fixed_rub": narrator_fixed_rub if narrator else character.get("manual_fixed_rub"),
                    "character_color": str(character.get("character_color") or ""),
                    "character_text_color": str(character.get("character_text_color") or ""),
                    "character_font_weight": str(character.get("character_font_weight") or ""),
                    "character_font_style": str(character.get("character_font_style") or ""),
                    "total_rub": row_total,
                }
            )
        return rows, {
            "character_total": character_total,
            "dialogue_lines": dialogue_lines,
            "dialogue_seconds": dialogue_seconds,
            "cast_only_count": cast_only_count,
            "unpriced_playable_roles": unpriced_playable_roles,
            "narrator_source_lines": narrator_source_lines,
            "narrator_source_seconds": narrator_source_seconds,
        }

    def api_book_budget(request: Request, book_id: str):
        access_error = require_budget_view_access(request)
        if access_error:
            return access_error
        with session_local() as db:
            book = get_book(db, book_id)
            if not book:
                return not_found_response()
            budget = db.query(book_budget_model).filter(book_budget_model.book_id == book_id).first()
            characters = db.query(character_model).filter(character_model.book_id == book_id).order_by(character_model.name.asc()).all()
            palette_changed = ensure_book_palette(characters) if characters else False
            if palette_changed:
                db.commit()
                characters = db.query(character_model).filter(character_model.book_id == book_id).order_by(character_model.name.asc()).all()
            snapshots = db.query(character_budget_snapshot_model).filter(character_budget_snapshot_model.book_id == book_id).all()
            narrator_rows = [row for row in characters if is_narrator_name(getattr(row, "name", "") or "") or is_narrator_name(getattr(row, "aliases", "") or "") or is_narrator_name(getattr(row, "narrator_role", "") or "")]
            character_ids = {str(getattr(item, "id", "") or "") for item in characters}
            snapshot_character_ids = {str(getattr(item, "character_id", "") or "") for item in snapshots}
            needs_autofill = (
                not snapshots
                or not character_ids.issubset(snapshot_character_ids)
                or bool(snapshot_character_ids - character_ids)
                or len(narrator_rows) > 1
                or all(
                    int(getattr(item, "lines_count", 0) or 0) <= 0
                    and int(getattr(item, "approx_seconds", 0) or 0) <= 0
                    for item in snapshots
                )
            )
            # Числа сметы — из разметки v2; устаревают, когда прогон или человек её меняет.
            needs_autofill = needs_autofill or v2_budget_is_stale(db, book_id)
            if str(book.status or "") not in {"queued", "processing", "stopping", "failed", "stalled", "char_extracting"} and needs_autofill:
                updater = session_display_name(request) or "system"
                rebuild_v2_budget(db, book_id, updated_by=updater)
                db.commit()
                budget = db.query(book_budget_model).filter(book_budget_model.book_id == book_id).first()
                characters = db.query(character_model).filter(character_model.book_id == book_id).order_by(character_model.name.asc()).all()
                palette_changed = ensure_book_palette(characters) if characters else False
                if palette_changed:
                    db.commit()
                    characters = db.query(character_model).filter(character_model.book_id == book_id).order_by(character_model.name.asc()).all()
                snapshots = db.query(character_budget_snapshot_model).filter(character_budget_snapshot_model.book_id == book_id).all()
            characters, snapshot_map = collapse_budget_character_rows(characters, snapshots)
            narrative = int((budget.narrative_cost_rub if budget else 0) or 0)
            engineer = int((budget.sound_engineer_cost_rub if budget else 0) or 0)
            extra = int((budget.extra_cost_rub if budget else 0) or 0)
            narrator_actor = (getattr(budget, "narrator_actor_name", "") if budget else "") or ""
            rows, summary_counts = build_character_rows(
                characters,
                snapshot_map,
                narrator_fixed_rub=narrative,
                narrator_actor_name=narrator_actor,
            )
            # Срок пробы или роли — у строки каста (спека 2026-10-01-role-deadlines).
            from app.services.role_deadlines import book_views

            deadline_by_character = book_views(db, book_id)
            for row in rows:
                row["deadline"] = deadline_by_character.get(str(row.get("character_id") or ""))
            audiobook_seconds = int(summary_counts["dialogue_seconds"]) + int(summary_counts["narrator_source_seconds"])
            has_narrator_row = any(bool(row.get("is_narrator")) for row in rows)
            if not has_narrator_row and (narrative > 0 or narrator_actor.strip()):
                rows.insert(
                    0,
                    {
                        "character_id": "narrator",
                        "char_map_id": "narrator",
                        "name": NARRATOR_DISPLAY_NAME,
                        "is_narrator": True,
                        "aliases": "",
                        "race": "",
                        "note": "",
                        "temperament": "",
                        "actor_name": narrator_actor,
                        "appears_in_chapters": [],
                        "chapter_count": 0,
                        "lines_count": 0,
                        "approx_seconds": 0,
                        "fact_seconds": 0,
                        "calc_mode": "fixed",
                        "manual_rate_rub_per_min": None,
                        "manual_fixed_rub": narrative,
                        "character_color": "",
                        "character_text_color": "",
                        "character_font_weight": "800",
                        "character_font_style": "normal",
                        "total_rub": narrative,
                    },
                )
            character_total = int(summary_counts["character_total"])
            grand_total = character_total + narrative + engineer + extra
            chapters = (
                db.query(script_chapter_model)
                .filter(script_chapter_model.book_id == book_id)
                .order_by(script_chapter_model.chapter_index.asc())
                .all()
            )
            processing_chapter_count = len(chapters)
            source_chapter_count = detect_author_chapter_count(chapters)
            warnings = []
            if narrative <= 0:
                warnings.append("narrator_fixed_missing")
            if summary_counts["cast_only_count"] > 0:
                warnings.append("cast_only_roles")
            if summary_counts["unpriced_playable_roles"] > 0:
                warnings.append("unpriced_playable_roles")
            if source_chapter_count and processing_chapter_count and source_chapter_count != processing_chapter_count:
                warnings.append("source_chapters_collapsed")
            return JSONResponse(
                {
                    "ok": True,
                    "book_id": book.id,
                    "book_title": book.display_title or book.title,
                    "book_annotation": (book.book_annotation or "") or "",
                    "urls": {
                        "recording": f"/app/recording?book_id={book.id}",
                    },
                    # the rate a role is priced at when it has none of its own
                    "default_rate_rub_per_min": default_rate_rub_per_min(db),
                    # rubles per dollar, for the model prices on the book hub
                    "usd_rub_rate": round(usd_rub_rate(db), 2),
                    "budget": {
                        "narrative_cost_rub": narrative,
                        "sound_engineer_cost_rub": engineer,
                        "extra_cost_rub": extra,
                        "notes": (budget.notes if budget else "") or "",
                        "narrator_actor_name": (getattr(budget, "narrator_actor_name", "") if budget else "") or "",
                        "updated_at": format_dt(budget.updated_at) if budget else None,
                    },
                    "characters": rows,
                    "totals": {
                        "character_total_rub": character_total,
                        "narrator_total_rub": narrative,
                        "sound_engineer_cost_rub": engineer,
                        "extra_cost_rub": extra,
                        "grand_total_rub": grand_total,
                    },
                    "summary": {
                        "source_chapter_count": source_chapter_count,
                        "processing_chapter_count": processing_chapter_count,
                        "dialogue_lines_count": int(summary_counts["dialogue_lines"]),
                        "dialogue_seconds": int(summary_counts["dialogue_seconds"]),
                        "audiobook_seconds": audiobook_seconds,
                        "narrator_pricing_mode": "fixed",
                        "narrator_source_lines_count": int(summary_counts["narrator_source_lines"]),
                        "narrator_source_seconds": int(summary_counts["narrator_source_seconds"]),
                        "cast_only_count": int(summary_counts["cast_only_count"]),
                        "priced_roles_count": sum(1 for row in rows if not row.get("is_narrator") and int(row.get("total_rub") or 0) > 0),
                        "unpriced_playable_roles_count": int(summary_counts["unpriced_playable_roles"]),
                        "calculation_warnings": warnings,
                    },
                }
            )

    async def api_save_book_budget(request: Request, book_id: str, payload: SaveBookBudgetPayload):
        access_error = require_budget_write_access(request)
        if access_error:
            return access_error
        uid = str(session_payload(request).get("uid") or "")
        with session_local() as db:
            budget = db.query(book_budget_model).filter(book_budget_model.book_id == book_id).first()
            if not budget:
                budget = book_budget_model(book_id=book_id)
                db.add(budget)
            budget.narrative_cost_rub = max(0, payload.narrative_cost_rub)
            budget.sound_engineer_cost_rub = max(0, payload.sound_engineer_cost_rub)
            budget.extra_cost_rub = max(0, payload.extra_cost_rub)
            budget.notes = payload.notes.strip()
            was_narrator_actor = str(getattr(budget, "narrator_actor_name", "") or "").strip()
            budget.narrator_actor_name = payload.narrator_actor_name.strip()
            budget.updated_by = uid
            budget.updated_at = utcnow_naive()
            book = get_book(db, book_id)
            rebuild_v2_budget(db, book_id, updated_by=uid)
            approval = None
            if should_notify_actor_change(was_narrator_actor, budget.narrator_actor_name):
                approval = notify_role_approved(
                    db, book_id=book_id, book_title=str(getattr(book, "title", "") or ""),
                    role=NARRATOR_DISPLAY_NAME, actor_name=budget.narrator_actor_name,
                )
            rebuild_assignments(db, book_id)
            db.commit()
        return {"ok": True, **({"approval": approval} if approval else {})}

    async def api_save_book_card(request: Request, book_id: str, payload: SaveBookCardPayload):
        access_error = require_budget_write_access(request)
        if access_error:
            return access_error
        with session_local() as db:
            book = get_book(db, book_id)
            if not book:
                return not_found_response()
            book.book_annotation = str(payload.book_annotation or "").strip()
            db.commit()
        return {"ok": True}

    async def api_save_budget_character(request: Request, char_id: str, payload: SaveBudgetCharacterPayload):
        """Карточка персонажа. Агенту здесь открыто одно поле — актёр, и то с вопросом."""
        if not is_authenticated(request):
            return unauthorized()
        scope = cast_assign_scope(request)
        if not scope:
            return forbidden()
        if scope == "actor_only":
            # `model_fields_set` — то, что клиент прислал явно; `is not None` спутал бы
            # «не трогали поле» с «прислали пустое», а снятие своего же предложения
            # приходит именно пустой строкой.
            if payload.model_fields_set - {"actor_name"}:
                return JSONResponse(
                    {"ok": False, "error": "agent_actor_only", "message": AGENT_FIELDS_REFUSAL},
                    status_code=403,
                )
        uid = str(session_payload(request).get("uid") or "")
        with session_local() as db:
            char = db.query(character_model).filter(character_model.id == char_id).first()
            if not char:
                return JSONResponse({"ok": False, "error": "Character not found"}, status_code=404)
            # Рассказчик — обычная строка `Character`, и агент дотягивается до неё по
            # адресу, минуя интерфейс. Проверять можно только с карточкой на руках,
            # поэтому отказ здесь, а не в `cast_assign_scope` выше.
            if scope == "actor_only" and (
                is_narrator_name(str(char.name or ""))
                or is_narrator_name(str(getattr(char, "aliases", "") or ""))
                or is_narrator_name(str(getattr(char, "narrator_role", "") or ""))
            ):
                return JSONResponse(
                    {"ok": False, "error": "agent_not_narrator", "message": AGENT_NARRATOR_REFUSAL},
                    status_code=403,
                )
            snapshot = db.query(character_budget_snapshot_model).filter(character_budget_snapshot_model.character_id == char.id).first()
            if not snapshot:
                snapshot = character_budget_snapshot_model(book_id=char.book_id, character_id=char.id)
                db.add(snapshot)
                db.flush()

            if payload.name is not None:
                next_name = payload.name.strip()
                if next_name:
                    char.name = next_name
            if payload.appears_in is not None:
                char.appears_in = normalize_appears_in(payload.appears_in)
            # Назначение на роль — не запись поля, а голос: у автора он весит два, у
            # владельца один, и решение пересчитывается из всех голосов сразу.
            was_actor = str(char.actor_name or "").strip()
            vote = None
            if payload.actor_name is not None:
                # Агент назначает предварительно всегда: знак дописывается за него.
                wanted = as_tentative(payload.actor_name) if scope == "actor_only" else payload.actor_name
                vote = cast_role_vote(
                    db,
                    character=char,
                    voter_uid=uid,
                    voter_name=session_display_name(request) or "",
                    weight=vote_weight(session_roles(request)),
                    actor_name=wanted,
                )
            if payload.race is not None:
                char.race = payload.race.strip()
            if payload.temperament is not None:
                char.temperament = payload.temperament.strip()
            if payload.manual_rate_rub_per_min is not None:
                char.manual_rate_rub_per_min = max(0, payload.manual_rate_rub_per_min) or None
            if payload.manual_fixed_rub is not None:
                char.manual_fixed_rub = max(0, payload.manual_fixed_rub) or None
            if payload.character_color is not None:
                char.character_color = normalize_hex_color(payload.character_color, fallback=char.character_color or "")
            if payload.character_text_color is not None:
                char.character_text_color = normalize_hex_color(payload.character_text_color, fallback=char.character_text_color or "")
            if payload.character_font_weight is not None:
                font_weight = str(payload.character_font_weight or "").strip()
                char.character_font_weight = font_weight if font_weight in {"400", "500", "600", "700", "800", "900"} else (char.character_font_weight or "")
            if payload.character_font_style is not None:
                font_style = str(payload.character_font_style or "").strip().lower()
                char.character_font_style = font_style if font_style in {"normal", "italic"} else (char.character_font_style or "")

            if payload.lines_count is not None:
                snapshot.lines_count = max(0, payload.lines_count)
            if payload.duration_seconds is not None:
                next_duration = max(0, payload.duration_seconds)
                snapshot.approx_seconds = next_duration
                snapshot.fact_seconds = next_duration

            snapshot.total_rub = calc_snapshot_total(
                int(snapshot.approx_seconds or 0),
                char.manual_rate_rub_per_min,
                char.manual_fixed_rub,
                int(snapshot.total_rub or 0),
                default_rate_rub_per_min(db),
            )
            snapshot.updated_at = utcnow_naive()
            char.updated_at = utcnow_naive()
            budget = db.query(book_budget_model).filter(book_budget_model.book_id == char.book_id).first()
            if budget:
                budget.updated_by = uid
                budget.updated_at = utcnow_naive()
            cycle = None
            if (vote and scope != "actor_only"
                    and vote["actor_name"] == " ".join(str(payload.actor_name or "").split())):
                # Утверждение — на персонажа цикла, а не на строку одной книги: профиль
                # автора ставит того же актёра во все книги, где роль не записана. Агент
                # только предлагает («Имя?»), и явная проверка не даст будущей правке
                # `as_tentative` молча открыть ему рекаст целого цикла. Несётся только
                # победивший голос: проигравший в этой книге не должен ставить победителя
                # в другие книги от имени проигравшего.
                cycle = propagate_approval(
                    db, char, voter_uid=uid, voter_name=session_display_name(request) or "",
                    weight=vote_weight(session_roles(request)),
                )
            # Утверждение на роль — новость для диктора, и до сих пор он узнавал о ней сам.
            approval = None
            cycle_changed = (cycle or {}).get("changed", [])
            if should_notify_actor_change(was_actor, str(char.actor_name or "").strip()):
                book = get_book(db, char.book_id)
                approval = notify_role_approved(
                    db, book_id=str(char.book_id), book_title=str(getattr(book, "title", "") or ""),
                    role=str(char.name or ""), actor_name=str(char.actor_name or ""),
                    also_in=[row["book_title"] for row in cycle_changed],
                )
            elif cycle_changed:
                # Здесь актёр уже стоял, но цикл поставил его в другие книги — о них и письмо.
                approval = notify_role_approved(
                    db, book_id=cycle_changed[0]["book_id"], book_title=cycle_changed[0]["book_title"],
                    role=str(char.name or ""), actor_name=str(char.actor_name or ""),
                    also_in=[row["book_title"] for row in cycle_changed[1:]],
                )
            # Карточка диктора читает свои роли из агрегата: пересобрать все книги,
            # которых коснулось назначение, — эту и те, куда оно ушло по циклу.
            for touched_book in {str(char.book_id), *[row["book_id"] for row in cycle_changed]}:
                rebuild_assignments(db, touched_book)
            db.commit()
        return {
            "ok": True,
            **({"approval": approval} if approval else {}),
            **({"vote": vote} if vote else {}),
            **({"cycle": cycle} if cycle and (cycle["changed"] or cycle["kept"] or cycle["outvoted"]) else {}),
        }

    async def api_save_studio_rate(request: Request, payload: SaveStudioRatePayload):
        """The studio's default rate. Books do not store it; they read it."""
        access_error = require_budget_write_access(request)
        if access_error:
            return access_error
        with session_local() as db:
            who = session_display_name(request) or ""
            rate = set_default_rate(db, payload.default_rate_rub_per_min, updated_by=who)
            if payload.usd_rub_rate is not None:
                set_usd_rub_rate(db, payload.usd_rub_rate, updated_by=who)
            usd = usd_rub_rate(db)
            db.commit()
        return {"ok": True, "default_rate_rub_per_min": rate, "usd_rub_rate": usd}

    def api_studio_rate(request: Request):
        if not is_authenticated(request):
            return unauthorized()
        with session_local() as db:
            row = studio_settings(db)
            rate = int(row.default_rate_rub_per_min or 0)
            usd = usd_rub_rate(db)
            db.commit()
        return {"ok": True, "default_rate_rub_per_min": rate, "usd_rub_rate": usd}

    return {
        "api_studio_rate": api_studio_rate,
        "api_save_studio_rate": api_save_studio_rate,
        "api_book_budget": api_book_budget,
        "api_save_book_budget": api_save_book_budget,
        "api_save_book_card": api_save_book_card,
        "api_save_budget_character": api_save_budget_character,
    }
