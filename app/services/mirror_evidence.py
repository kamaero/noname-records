"""Подтверждение NAS связывает прочитанные байты с нынешним ключом без I/O из HTTP."""
from __future__ import annotations

import hashlib
import json
import os


def _value(item, name):
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


def _root(root):
    return hashlib.sha256(os.path.abspath(str(root or "")).encode()).hexdigest()


def _digest(proof):
    return hashlib.sha256(json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def make_mirror_proof(item, *, checksum, size, root):
    proof = {"version": 1, "audio_id": str(_value(item, "id") or _value(item, "audio_id")),
             "source_key": str(_value(item, "stored_key") or ""),
             "source_md5": str(_value(item, "md5") or ""),
             "source_size": int(_value(item, "size_bytes") or 0),
             "destination_key": str(_value(item, "stored_key") or ""),
             "destination_root": _root(root), "copied_md5": checksum, "copied_size": size}
    return json.dumps(dict(proof, digest=_digest(proof)), sort_keys=True)


def mirror_proof_valid(item, *, root):
    try:
        proof = json.loads(_value(item, "mirror_proof_json") or "{}")
        digest = proof.pop("digest")
        checksum = str(_value(item, "md5") or "")
        size = int(_value(item, "size_bytes") or 0)
        return (proof.get("version") == 1 and digest == _digest(proof) and bool(checksum) and size > 0
                and proof.get("audio_id") == str(_value(item, "id") or _value(item, "audio_id"))
                and proof.get("source_key") == proof.get("destination_key") == _value(item, "stored_key")
                and proof.get("source_md5") == proof.get("copied_md5") == checksum
                and proof.get("source_size") == proof.get("copied_size") == size
                and proof.get("destination_root") == _root(root))
    except (TypeError, ValueError, KeyError, AttributeError):
        return False
