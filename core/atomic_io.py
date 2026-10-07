"""Atomic JSON publish/read for the telemetry plane (Windows-safe).

Writers: unique tmp file in the same directory -> flush -> os.replace. A reader
sees the old file or the new one, never a torn write. On Windows os.replace
raises PermissionError while another process holds the target open (the UI
server reading it); that window is microseconds, so a short bounded retry
wins without ever blocking the trading loop for more than ~60 ms.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional


def json_safe(o: Any) -> Any:
    """NaN/inf -> None recursively, so json.dumps(allow_nan=False) never raises."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [json_safe(v) for v in o]
    return o


def atomic_write_json(path, obj: Any, retries: int = 6, delay: float = 0.005, fsync: bool = False) -> bool:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(json_safe(obj), ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(data)
        fh.flush()
        if fsync:
            os.fsync(fh.fileno())
    for i in range(max(1, retries)):
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:
            time.sleep(delay * (2 ** i))
    try:
        os.remove(tmp)
    except OSError:
        pass
    return False


def read_json(path, retries: int = 3, delay: float = 0.003) -> Optional[Any]:
    path = Path(path)
    for i in range(max(1, retries)):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            time.sleep(delay * (i + 1))
    return None
