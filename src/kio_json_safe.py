from __future__ import annotations

from pathlib import Path
from typing import Any


CIRCULAR_MARKER = "<circular-reference>"


def make_json_safe(value: Any, *, _active: set[int] | None = None) -> Any:
    """Return a JSON-serializable copy while cutting true recursive cycles.

    Shared objects that are not recursive are copied normally. Only objects that
    re-enter the current recursion path are replaced with CIRCULAR_MARKER.
    """
    active = _active if _active is not None else set()

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)

    if isinstance(value, dict):
        obj_id = id(value)
        if obj_id in active:
            return CIRCULAR_MARKER
        active.add(obj_id)
        try:
            return {str(key): make_json_safe(item, _active=active) for key, item in value.items()}
        finally:
            active.remove(obj_id)

    if isinstance(value, (list, tuple, set, frozenset)):
        obj_id = id(value)
        if obj_id in active:
            return CIRCULAR_MARKER
        active.add(obj_id)
        try:
            return [make_json_safe(item, _active=active) for item in value]
        finally:
            active.remove(obj_id)

    return str(value)
