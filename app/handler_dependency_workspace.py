from __future__ import annotations

from app.services.audio_uploads import (
    apply_batch_overrides as _apply_batch_overrides_service,
    build_canonical_audio_filename as _build_canonical_audio_filename,
    parse_batch_audio_filename as _parse_batch_audio_filename_service,
    store_audio_file as _store_audio_file_service,
)
from app.services.book_progress import collect_books_progress as _collect_books_progress
from app.services.character_colors import build_character_style_maps as _build_character_style_maps
from app.services.owner_dashboard import build_owner_dashboard_data
from app.services.recording import recording_identity as _recording_identity
from app.services.recording_workspace import build_recording_workspace_payload as _recording_workspace_payload
from app.services.telegram import send_telegram_message as _send_telegram_message


def build_workspace_service_deps() -> dict:
    return {
        "collect_books_progress": _collect_books_progress,
        "build_owner_dashboard_data": build_owner_dashboard_data,
        "build_character_style_maps": _build_character_style_maps,
        "build_canonical_audio_filename": _build_canonical_audio_filename,
        "parse_batch_audio_filename_service": _parse_batch_audio_filename_service,
        "apply_batch_overrides_service": _apply_batch_overrides_service,
        "store_audio_file_service": _store_audio_file_service,
        "send_telegram_message": _send_telegram_message,
        "recording_identity": _recording_identity,
        "recording_workspace_payload": _recording_workspace_payload,
    }
