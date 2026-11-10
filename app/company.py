"""회사 현황 (data/company.json) — 그룹사·컨소시엄 협력사별 자유 형식 소개 텍스트.

워크스페이스마다 제안에 참여하는 회사(1개 이상, 첫 번째 = 주관사)를 고르면
추진 전략 추천 시 선택한 회사들의 현황을 종합해 반영한다.

저장 형식: {"companies": [{"id", "name", "kind": "group"|"partner", "profile", "updated_at"}]}
예전 형식({"profile", "updated_at"} 한 개)은 처음 읽을 때 '시큐리티콘텐츠허브' 현황으로 옮긴다.
"""
import json
import re
import threading
import uuid

from . import storage

_lock = threading.RLock()
MAX_CHARS = 30000
KINDS = {"group": "그룹사", "partner": "컨소시엄 협력사"}
# 처음 실행할 때 만들어 두는 그룹사 (현황은 비어 있음)
SEED = [("c-sch", "시큐리티콘텐츠허브"), ("c-securityhub", "시큐리티허브"), ("c-kisec", "한국정보보호교육센터")]


def _file():
    return storage.DATA_DIR / "company.json"


def _write(db: dict) -> None:
    _file().parent.mkdir(parents=True, exist_ok=True)
    tmp = _file().with_suffix(".tmp")
    tmp.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_file())


def _load() -> dict:
    with _lock:
        try:
            data = json.loads(_file().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if isinstance(data.get("companies"), list):
            return data
        # 예전 단일 현황 → 첫 그룹사(시큐리티콘텐츠허브)로 옮기고 나머지 그룹사를 만든다
        companies = [{"id": cid, "name": name, "kind": "group", "profile": "", "updated_at": ""} for cid, name in SEED]
        if data.get("profile"):
            companies[0]["profile"] = data["profile"]
            companies[0]["updated_at"] = data.get("updated_at", "")
        db = {"companies": companies}
        _write(db)
        return db


def all_companies() -> list[dict]:
    return _load()["companies"]


def get(cid: str) -> dict | None:
    return next((c for c in all_companies() if c["id"] == cid), None)


def default_ids() -> list[str]:
    """컨소시엄을 고르지 않은 워크스페이스의 기본값 = 첫 번째 회사."""
    cs = all_companies()
    return [cs[0]["id"]] if cs else []


def resolve(ids: list | None) -> list[dict]:
    """워크스페이스에 저장된 회사 id 목록 → 회사 정보(없어진 회사는 빼고, 비면 기본값)."""
    by_id = {c["id"]: c for c in all_companies()}
    picked = [by_id[i] for i in (ids or []) if i in by_id]
    return picked or [by_id[i] for i in default_ids()]


def _clean(name: str, kind: str, profile: str | None) -> tuple[str, str, str | None]:
    name = re.sub(r"\s+", " ", name or "").strip()
    if not name:
        raise ValueError("회사 이름을 입력하세요.")
    if len(name) > 60:
        raise ValueError("회사 이름은 60자 이내로 입력하세요.")
    if kind not in KINDS:
        raise ValueError("구분은 그룹사 또는 컨소시엄 협력사입니다.")
    if profile is not None:
        profile = profile.replace("\r\n", "\n").strip()
        if len(profile) > MAX_CHARS:
            raise ValueError(f"회사 현황은 {MAX_CHARS:,}자 이내로 입력하세요. (현재 {len(profile):,}자)")
    return name, kind, profile


def create(name: str, kind: str = "partner") -> dict:
    name, kind, _ = _clean(name, kind, None)
    with _lock:
        db = _load()
        if any(c["name"] == name for c in db["companies"]):
            raise ValueError("같은 이름의 회사가 이미 있습니다.")
        c = {"id": "c-" + uuid.uuid4().hex[:8], "name": name, "kind": kind, "profile": "", "updated_at": ""}
        db["companies"].append(c)
        _write(db)
    return c


def update(cid: str, name: str, kind: str, profile: str) -> dict:
    name, kind, profile = _clean(name, kind, profile)
    with _lock:
        db = _load()
        c = next((x for x in db["companies"] if x["id"] == cid), None)
        if c is None:
            raise ValueError("회사를 찾을 수 없습니다.")
        if any(x["name"] == name and x["id"] != cid for x in db["companies"]):
            raise ValueError("같은 이름의 회사가 이미 있습니다.")
        if profile != c.get("profile"):
            c["updated_at"] = storage.now()
        c.update(name=name, kind=kind, profile=profile)
        _write(db)
    return c


def delete(cid: str) -> None:
    with _lock:
        db = _load()
        if len(db["companies"]) <= 1:
            raise ValueError("회사가 하나는 있어야 합니다.")
        before = len(db["companies"])
        db["companies"] = [c for c in db["companies"] if c["id"] != cid]
        if len(db["companies"]) == before:
            raise ValueError("회사를 찾을 수 없습니다.")
        _write(db)


def signature(companies: list[dict]) -> str:
    """전략 결과가 어느 회사 구성·현황 저장본으로 만들어졌는지 비교하는 값."""
    return "|".join(f"{c['id']}@{c.get('updated_at', '')}" for c in companies)


def prompt_block(companies: list[dict]) -> str:
    """추진 전략 프롬프트에 넣을 회사(컨소시엄) 현황. 현황이 하나도 없으면 ""."""
    if not any(c.get("profile", "").strip() for c in companies):
        return ""
    names = [c["name"] for c in companies]
    head = (f"[컨소시엄 구성] 주관사: {names[0]}" + (f" / 참여사: {', '.join(names[1:])}" if len(names) > 1 else " (단독 참여)"))
    parts = [head]
    for i, c in enumerate(companies):
        role = "주관사" if i == 0 else "참여사"
        body = c.get("profile", "").strip() or "(현황 미등록 — 이 회사의 강점은 근거로 쓰지 마세요)"
        parts.append(f'<company name="{c["name"]}" role="{role}" kind="{KINDS.get(c.get("kind"), "")}">\n{body}\n</company>')
    return "\n".join(parts)
