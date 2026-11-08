"""Complete, immutable messages for a persistent Hermes process.

The producer publishes a complete file atomically; consumers never inspect
temporary files. Receipt and completion are separate messages using the same
turn ID. No terminal keystrokes or timing heuristic delimit a message.
"""
import hashlib
import json
import os
import re
import uuid
from pathlib import Path


class ChannelError(ValueError):
    pass


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ChannelError("Invalid channel message identifier")
    return value


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


class HermesChannel:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, turn_id, kind):
        return self.directory / f"{_identifier(turn_id)}.{_identifier(kind)}.json"

    def receive(self, turn_id, kind):
        path = self._path(turn_id, kind)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            envelope = json.loads(raw)
            payload = envelope["payload"]
            encoded = _bytes(payload)
            valid = (envelope["version"] == 1 and envelope["turn_id"] == turn_id
                     and envelope["kind"] == kind and isinstance(payload, dict)
                     and envelope["bytes"] == len(encoded)
                     and envelope["sha256"] == hashlib.sha256(encoded).hexdigest())
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise ChannelError("Malformed channel message") from exc
        if not valid:
            raise ChannelError("Channel message identity or integrity mismatch")
        return payload

    def publish(self, turn_id, kind, payload):
        """Return False for an identical retry; reject conflicting reuse of an ID."""
        destination = self._path(turn_id, kind)
        if not isinstance(payload, dict):
            raise ChannelError("Channel payload must be an object")
        encoded = _bytes(payload)
        envelope = {"version": 1, "turn_id": turn_id, "kind": kind,
                    "bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest(),
                    "payload": payload}
        temporary = self.directory / f".{uuid.uuid4().hex}.pending"
        try:
            with temporary.open("xb") as handle:
                handle.write(_bytes(envelope))
                handle.flush()
                os.fsync(handle.fileno())
            try:
                # Windows rename refuses an existing destination. POSIX rename
                # overwrites, so use a no-overwrite hard link there instead.
                # replace() would silently lose a concurrent input.
                if os.name == "nt":
                    os.rename(temporary, destination)
                else:
                    os.link(temporary, destination)
            except FileExistsError:
                if self.receive(turn_id, kind) != payload:
                    raise ChannelError("Message ID already contains different input")
                return False
            return True
        finally:
            temporary.unlink(missing_ok=True)
