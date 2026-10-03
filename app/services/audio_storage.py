from __future__ import annotations

import hashlib
import os
import shutil

from app.config import settings


def local_root() -> str:
    """Основное хранилище на диске сервера."""
    path = (settings.audio_storage_path or "").strip()
    if not path:
        raise RuntimeError("AUDIO_STORAGE_PATH not configured. Set it in .env.")
    return path


def nas_root() -> str:
    """Зеркало на NAS владельца. Пустая настройка — не повод падать: без неё
    приём работает как обычно, просто второй копии не появляется."""
    return (settings.audio_nas_path or "").strip()


def _root_for(location: str) -> str:
    if str(location or "local") != "nas":
        return local_root()
    root = nas_root()
    if not root:
        # Пустой корень плюс относительный ключ — это путь от рабочего каталога
        # процесса. Отсутствие зеркала законно, приём от него не зависит, но
        # адресовать внутри несуществующего корня нечего: молчаливая запись рядом
        # с кодом хуже отказа, потому что выглядит как удача.
        raise RuntimeError("AUDIO_NAS_PATH not configured. Set it in .env.")
    return root


def resolve_path(key: str, *, location: str = "local") -> str:
    root = _root_for(location)
    candidate = os.path.normpath(os.path.join(root, key.lstrip("/")))
    # Defense-in-depth: ключи генерируются сервером (uuid), но не позволяем
    # никакому ключу выйти за пределы корня хранилища ("../" и абсолютные пути).
    root_abs = os.path.abspath(root)
    candidate_abs = os.path.abspath(candidate)
    if candidate_abs != root_abs and not candidate_abs.startswith(root_abs + os.sep):
        raise ValueError(f"unsafe storage key escapes root: {key!r}")
    return candidate


def free_bytes(path: str) -> int:
    """Сколько места осталось там, где лежит указанный путь."""
    target = path
    while target and not os.path.isdir(target):
        parent = os.path.dirname(target)
        if parent == target:
            break
        target = parent
    return int(shutil.disk_usage(target or "/").free)


def write_file(key: str, data: bytes, *, location: str = "local") -> str:
    """Записать байты и вернуть их md5."""
    path = resolve_path(key, location=location)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return hashlib.md5(data).hexdigest()


def move_file(src_key: str, dst_key: str, *, location: str = "local") -> None:
    """Переименовать файл внутри одного корня. Оба ключа проверяются тем же `resolve_path`."""
    src = resolve_path(src_key, location=location)
    dst = resolve_path(dst_key, location=location)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.replace(src, dst)


def write_stream(key: str, source, chunk_size: int = 1024 * 1024, *, location: str = "local") -> tuple[int, str]:
    """Скопировать поток в хранилище кусками. Возвращает размер и md5.

    Дубль Рассказчика — это гигабайт: прочитать его целиком в память, чтобы тут же
    записать на диск, значит выбирать между отказом по размеру и падением сервера.

    Сумма считается здесь же, в том же цикле: кусок уже в памяти, хеширование не
    добавляет ни одного лишнего чтения. Отдельный проход по готовому файлу стоил бы
    второго гигабайта ввода-вывода — по сети, если файл лёг на NAS.
    """
    path = resolve_path(key, location=location)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    written = 0
    digest = hashlib.md5()
    with open(path, "wb") as target:
        while True:
            chunk = source.read(chunk_size)
            if not chunk:
                break
            target.write(chunk)
            digest.update(chunk)
            written += len(chunk)
    return written, digest.hexdigest()


def read_file(key: str, *, location: str = "local") -> bytes:
    with open(resolve_path(key, location=location), "rb") as f:
        return f.read()


def read_range(key: str, start: int, length: int, *, location: str = "local") -> bytes:
    """Кусок файла, не весь файл.

    Проба весит десятки мегабайт, а перемотка просит тысячу байт: читать ради этого
    всё — значит держать в памяти по файлу на каждого слушателя.
    """
    with open(resolve_path(key, location=location), "rb") as f:
        f.seek(max(0, int(start)))
        return f.read(max(0, int(length)))


def file_size(key: str, *, location: str = "local") -> int:
    return os.path.getsize(resolve_path(key, location=location))


def file_exists(key: str, *, location: str = "local") -> bool:
    return os.path.isfile(resolve_path(key, location=location))
