"""Prompt checkpoints for edits performed by the built-in file tools."""
from __future__ import annotations

import base64
import difflib
import stat
import threading
from pathlib import Path
from typing import Any


class RewindHistory:
    def __init__(self, records: dict[str, Any] | None = None) -> None:
        self.records = records or {}
        self.active: str | None = None
        self._lock = threading.RLock()

    def begin(self, message_id: str) -> None:
        self.records.setdefault(message_id, {})
        self.active = message_id

    def write(self, path: Path, content: str) -> None:
        # Snapshot and write together so failed writes cannot create a false checkpoint.
        with self._lock:
            before = path.read_bytes() if path.exists() else None
            path.write_text(content, encoding="utf-8")
            after = path.read_bytes()
            if self.active is None:
                return
            changes = self.records[self.active]
            key = str(path.absolute())
            original = changes[key]["before"] if key in changes else _encode(before)
            changes[key] = {"before": original, "after": _encode(after),
                            "conflict": bool(key in changes and (changes[key].get("conflict")
                                or changes[key]["after"] != _encode(before)))}

    def since(self, message_ids: list[str]) -> list[str]:
        if not message_ids or message_ids[0] not in self.records:
            return message_ids
        keys = list(self.records)
        trailing = keys[keys.index(message_ids[0]):]
        return trailing + [mid for mid in message_ids if mid not in trailing]

    def changes(self, message_ids: list[str]) -> dict[str, dict]:
        changes: dict[str, dict] = {}
        for message_id in message_ids:
            for path, change in self.records.get(message_id, {}).items():
                if path in changes:
                    changes[path]["conflict"] = bool(changes[path].get("conflict")
                        or change.get("conflict") or changes[path]["after"] != change["before"])
                    changes[path]["after"] = change["after"]
                else:
                    changes[path] = dict(change)
        return {path: change for path, change in changes.items()
                if change["before"] != change["after"] or change.get("conflict")}

    def preview(self, message_ids: list[str]) -> dict:
        changes = self.changes(message_ids)
        added = removed = 0
        for change in changes.values():
            before = (_decode(change["before"]) or b"").decode("utf-8", errors="replace").splitlines()
            after = (_decode(change["after"]) or b"").decode("utf-8", errors="replace").splitlines()
            for line in difflib.ndiff(before, after):
                added += line.startswith("+ ")
                removed += line.startswith("- ")
        return {"files": list(changes), "added": added, "removed": removed,
                "can_restore_code": all(mid in self.records for mid in message_ids)
                    and not any(change.get("conflict") for change in changes.values())}

    def restore(self, message_ids: list[str]) -> list[str]:
        if not all(mid in self.records for mid in message_ids):
            raise ValueError("Code checkpoints are unavailable for these messages")
        changes = self.changes(message_ids)
        if any(change.get("conflict") for change in changes.values()):
            raise ValueError("Files changed outside tracked tools between checkpoints")
        # Validate the entire set before touching any file; manual/Bash edits stay intact.
        for name, change in changes.items():
            path = Path(name)
            if path.resolve() != path.absolute():
                raise ValueError(f"File path changed to a symbolic link: {name}")
            if path.exists():
                info = path.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError(f"File is not an independent regular file: {name}")
            current = path.read_bytes() if path.exists() else None
            if current != _decode(change["after"]):
                raise ValueError(f"File changed outside tracked tools: {name}")
        restored: list[str] = []
        try:
            for name, change in changes.items():
                path = Path(name)
                original = _decode(change["before"])
                if original is None:
                    path.unlink()
                else:
                    path.write_bytes(original)
                restored.append(name)
        except OSError:
            for name in reversed(restored):
                Path(name).write_bytes(_decode(changes[name]["after"]) or b"")
            raise
        for mid in message_ids:
            self.records[mid] = {}
        return restored


def _encode(value: bytes | None) -> str | None:
    return base64.b64encode(value).decode("ascii") if value is not None else None


def _decode(value: str | None) -> bytes | None:
    return base64.b64decode(value, validate=True) if value is not None else None


def write_tracked_file(context: Any, path: Path, content: str) -> None:
    history = getattr(context, "rewind_history", None)
    if history is None:
        path.write_text(content, encoding="utf-8")
    else:
        history.write(path, content)
