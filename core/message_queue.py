"""Atomic snapshots of pending messages, activated only by the owning runtime."""
import copy
import json
import math
import os
import threading
import uuid
from pathlib import Path


class DurableMessageQueue(list):
    def __init__(self, path):
        super().__init__()
        self.path = Path(path)
        self._active = False
        self._saved = []
        self._lock = threading.RLock()

    @staticmethod
    def _validate(items):
        if not isinstance(items, list):
            raise ValueError('排队消息快照必须为数组，原文件保留')
        ids = set()
        for item in items:
            if (not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id']
                    or item['id'] in ids or not isinstance(item.get('text'), str)
                    or item.get('mode') not in ('queue', 'interrupt')
                    or (item.get('session') is not None and not isinstance(item['session'], str))):
                raise ValueError('排队消息快照无效，原文件保留')
            timestamp = item.get('queued_at', 0)
            if not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or timestamp < 0:
                raise ValueError('排队消息时间无效，原文件保留')
            ids.add(item['id'])

    def activate(self):
        with self._lock:
            if self._active:
                return
            data = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else list(self)
            self._validate(data)
            super().clear()
            super().extend(copy.deepcopy(data))
            self._saved = copy.deepcopy(data)
            self._active = True

    def checkpoint(self):
        with self._lock:
            if not self._active:
                return
            temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                data = copy.deepcopy(list(self))
                self._validate(data)
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with temporary.open('w', encoding='utf-8', newline='\n') as stream:
                    json.dump(data, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                self._saved = data
            except Exception:
                super().clear()
                super().extend(copy.deepcopy(self._saved))
                raise
            finally:
                temporary.unlink(missing_ok=True)

    def _change(self, method, *args):
        with self._lock:
            result = method(*args)
            self.checkpoint()
            return result

    def append(self, value): return self._change(super().append, value)
    def extend(self, values): return self._change(super().extend, values)
    def insert(self, index, value): return self._change(super().insert, index, value)
    def remove(self, value): return self._change(super().remove, value)
    def pop(self, index=-1): return self._change(super().pop, index)
    def clear(self): return self._change(super().clear)
    def __setitem__(self, key, value): return self._change(super().__setitem__, key, value)
    def __delitem__(self, key): return self._change(super().__delitem__, key)
