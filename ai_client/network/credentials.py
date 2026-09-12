"""Atomic credential and resume-checkpoint storage."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .types import CredentialError, CredentialStore, SessionCheckpoint


class FileCredentialStore:
    """Persist one seat's token and last contiguous server sequence atomically."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    async def load(self) -> SessionCheckpoint | None:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as error:
            raise CredentialError("credential file could not be read") from error
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise CredentialError("credential file is not valid JSON") from error
        if not isinstance(value, dict):
            raise CredentialError("credential file has an invalid shape")
        fields = set(value)
        if fields == {"connection_token", "last_seq"}:
            # Preserve the historical checkpoint as identifiable 1.0 data.  The
            # active client decides whether that version may be resumed.
            protocol_version = "1.0"
        elif fields == {"connection_token", "last_seq", "protocol_version"}:
            protocol_version = value.get("protocol_version")
        else:
            raise CredentialError("credential file has an invalid shape")
        token = value.get("connection_token")
        last_seq = value.get("last_seq")
        try:
            return SessionCheckpoint(token, last_seq, protocol_version)
        except (TypeError, ValueError) as error:
            raise CredentialError("credential file has invalid values") from error

    async def save(self, checkpoint: SessionCheckpoint) -> None:
        if not isinstance(checkpoint, SessionCheckpoint):
            raise CredentialError("save expects a SessionCheckpoint")
        parent = self.path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=parent
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    json.dump(
                        {
                            "connection_token": checkpoint.connection_token,
                            "last_seq": checkpoint.last_seq,
                            "protocol_version": checkpoint.protocol_version,
                        },
                        stream,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_name, self.path)
            finally:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
        except OSError as error:
            raise CredentialError("credential checkpoint could not be saved") from error


__all__ = ["CredentialStore", "FileCredentialStore"]
