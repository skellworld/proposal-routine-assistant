"""우리 회사 현황 (data/company.json) — 추진 전략 추천 시 참고하는 자유 형식 소개 텍스트."""
import json
import threading

from . import storage

_lock = threading.Lock()
MAX_CHARS = 30000


def _file():
    return storage.DATA_DIR / "company.json"


def load() -> dict:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return {"profile": data.get("profile", ""), "updated_at": data.get("updated_at", "")}


def profile() -> str:
    return load()["profile"].strip()


def save(text: str) -> dict:
    text = (text or "").replace("\r\n", "\n").strip()
    if len(text) > MAX_CHARS:
        raise ValueError(f"회사 현황은 {MAX_CHARS:,}자 이내로 입력하세요. (현재 {len(text):,}자)")
    data = {"profile": text, "updated_at": storage.now()}
    with _lock:
        _file().parent.mkdir(parents=True, exist_ok=True)
        tmp = _file().with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_file())
    return data
