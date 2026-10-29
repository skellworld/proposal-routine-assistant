"""앱 설정 (data/settings.json)."""
import json
import threading

from . import storage

_lock = threading.Lock()


def _file():
    return storage.DATA_DIR / "settings.json"


def load() -> dict:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def get(key: str, default=None):
    return load().get(key, default)


def update(values: dict) -> dict:
    with _lock:
        data = {**load(), **values}
        _file().parent.mkdir(parents=True, exist_ok=True)
        _file().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data
