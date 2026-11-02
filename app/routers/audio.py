from collections.abc import Callable

from fastapi import FastAPI


def register_audio_routes(app: FastAPI, *, handlers: dict[str, Callable]) -> None:
    app.add_api_route("/dictor-pro/batch-validate", handlers["batch_validate_uploads"], methods=["POST"])
    # Тот же предпросмотр по адресу раздела записи: `/dictor-pro/*` скрыт в «одном месте»,
    # а старый адрес остаётся на VPS для вкладок со старым фронтом.
    app.add_api_route("/api/recording/batch-validate", handlers["batch_validate_uploads"], methods=["POST"])
    app.add_api_route("/api/recording/replica-patch", handlers["recording_replica_patch"], methods=["POST"])
    app.add_api_route("/api/recording/batch", handlers["recording_batch"], methods=["POST"])
    app.add_api_route("/api/recording/workspace", handlers["recording_workspace"], methods=["GET"])
    app.add_api_route("/api/recording/filename", handlers["recording_filename_hint"], methods=["GET"])
    app.add_api_route("/api/recording/files/{audio_id}/delete", handlers["recording_delete_audio"], methods=["POST"])
