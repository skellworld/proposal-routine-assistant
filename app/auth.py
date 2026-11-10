"""사용자 계정 · 로그인 세션 · 개인 AI 연결 설정 (data/users.json).

- 비밀번호: PBKDF2-SHA256 해시만 저장한다.
- 세션: 서버 비밀키로 서명한 쿠키(사용자명|만료|비밀번호 해시 일부). 비밀번호를 바꾸면 기존 세션이 끊긴다.
- 개인 API 키: data/secret.key로 암호화(Fernet)해 저장하고, 화면에는 끝 4자리만 보여 준다.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time

from cryptography.fernet import Fernet, InvalidToken

from . import storage

DATA = storage.DATA_DIR
USERS_FILE = DATA / "users.json"
SECRET_FILE = DATA / "secret.key"
COOKIE = "prh_session"
SESSION_DAYS = 14
PROVIDERS = {"anthropic": "Claude (Anthropic)", "openai": "ChatGPT (OpenAI)",
             "worker": "내 PC의 Claude Code (분석 실행기)", "cli": "서버 PC의 Pro 계정 (Claude Code)",
             "none": "AI 연결 안 함"}
_lock = threading.RLock()
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")


def _secret() -> bytes:
    with _lock:
        if not SECRET_FILE.exists():
            DATA.mkdir(parents=True, exist_ok=True)
            SECRET_FILE.write_bytes(Fernet.generate_key())
        return SECRET_FILE.read_bytes().strip()


def _fernet() -> Fernet:
    return Fernet(_secret())


def _load() -> dict:
    if not USERS_FILE.exists():
        return {"users": {}}
    return json.loads(USERS_FILE.read_text(encoding="utf-8"))


def _save(db: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = USERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(USERS_FILE)


# ---------------- 비밀번호 ----------------
def _hash_pw(pw: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 310_000)
    return f"pbkdf2${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def _check_pw(pw: str, stored: str) -> bool:
    try:
        _, salt, _ = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(_hash_pw(pw, base64.b64decode(salt)), stored)


def _validate(username: str, password: str | None) -> None:
    if not _NAME_RE.match(username or ""):
        raise ValueError("아이디는 영문·숫자·._- 3~32자로 입력하세요.")
    if password is not None and len(password) < 8:
        raise ValueError("비밀번호는 8자 이상으로 입력하세요.")


# ---------------- 계정 ----------------
def has_users() -> bool:
    return bool(_load()["users"])


def public(u: dict | None) -> dict | None:
    """화면·API로 내보낼 사용자 정보 (해시·키 제외)."""
    if not u:
        return None
    ai = u.get("ai") or {}
    return {"username": u["username"], "name": u.get("name") or u["username"], "role": u.get("role", "member"),
            "created_at": u.get("created_at", ""), "must_change_pw": bool(u.get("must_change_pw")),
            "ai": {"provider": ai.get("provider", ""), "model": ai.get("model", ""),
                   "key_hint": ai.get("key_hint", ""), "ready": ai_ready(u), "enabled": ai_enabled(u)},
            "worker_token": bool(u.get("worker_hash")), "worker_issued_at": u.get("worker_issued_at", "")}


def get(username: str) -> dict | None:
    u = _load()["users"].get(username)
    return {**u, "username": username} if u else None


def list_users() -> list[dict]:
    return [public({**u, "username": n}) for n, u in sorted(_load()["users"].items())]


def create(username: str, password: str, name: str = "", role: str = "member", must_change_pw: bool = False) -> dict:
    username = (username or "").strip()
    _validate(username, password)
    with _lock:
        db = _load()
        if username in db["users"]:
            raise ValueError("이미 있는 아이디입니다.")
        db["users"][username] = {"name": (name or "").strip() or username, "role": role,
                                 "pw": _hash_pw(password), "must_change_pw": must_change_pw,
                                 "created_at": storage.now(), "ai": {}}
        _save(db)
    return get(username)


def _update(username: str, fn) -> dict:
    with _lock:
        db = _load()
        u = db["users"].get(username)
        if u is None:
            raise ValueError("사용자를 찾을 수 없습니다.")
        fn(u)
        _save(db)
    return get(username)


def set_password(username: str, password: str, must_change: bool = False) -> dict:
    _validate(username, password)

    def f(u):
        u["pw"] = _hash_pw(password)
        u["must_change_pw"] = must_change
    return _update(username, f)


def _admins() -> int:
    return sum(1 for x in list_users() if x["role"] == "admin")


def set_role(username: str, role: str) -> dict:
    if role not in ("admin", "member"):
        raise ValueError("역할은 admin 또는 member입니다.")
    u = get(username)
    if u is None:
        raise ValueError("사용자를 찾을 수 없습니다.")
    if role == "member" and u.get("role") == "admin" and _admins() <= 1:
        raise ValueError("관리자가 한 명은 있어야 합니다.")
    return _update(username, lambda x: x.__setitem__("role", role))


def delete(username: str) -> None:
    u = get(username)
    if u is None:
        raise ValueError("사용자를 찾을 수 없습니다.")
    if u.get("role") == "admin" and _admins() <= 1:
        raise ValueError("마지막 관리자는 삭제할 수 없습니다.")
    with _lock:
        db = _load()
        db["users"].pop(username, None)
        _save(db)


# ---------------- 로그인 (시도 횟수 제한) ----------------
_fails: dict = {}  # ip -> [실패 시각...]


def login(username: str, password: str, ip: str) -> dict:
    now = time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < 600]
    if len(recent) >= 10:
        raise ValueError("로그인 실패가 너무 많습니다. 10분 뒤 다시 시도하세요.")
    u = get((username or "").strip())
    if not u or not _check_pw(password or "", u["pw"]):
        _fails[ip] = recent + [now]
        raise ValueError("아이디 또는 비밀번호가 맞지 않습니다.")
    _fails.pop(ip, None)
    return u


# ---------------- 세션 쿠키 ----------------
def _sign(msg: str) -> str:
    return hmac.new(_secret(), msg.encode(), hashlib.sha256).hexdigest()


def make_session(u: dict) -> str:
    exp = int(time.time()) + SESSION_DAYS * 86400
    msg = f"{u['username']}|{exp}|{u['pw'][-12:]}"
    return f"{msg}|{_sign(msg)}"


def read_session(token: str | None) -> dict | None:
    try:
        name, exp, ver, sig = (token or "").split("|")
        if not hmac.compare_digest(_sign(f"{name}|{exp}|{ver}"), sig) or int(exp) < time.time():
            return None
    except ValueError:
        return None
    u = get(name)
    if not u or u["pw"][-12:] != ver:
        return None
    return u


# ---------------- 개인 AI 연결 ----------------
def ai_enabled(u: dict | None) -> bool:
    """AI 분석 기능을 쓸 수 있는지 ('AI 연결 안 함'이거나 설정 전이면 False)."""
    return bool(u) and (u.get("ai") or {}).get("provider") != "none" and ai_ready(u)


def ai_ready(u: dict) -> bool:
    """최초 설정(AI 선택)을 마쳤는지 — 'AI 연결 안 함'도 선택을 마친 것으로 본다."""
    ai = u.get("ai") or {}
    if ai.get("provider") in ("cli", "none"):
        return True
    if ai.get("provider") == "worker":
        return bool(u.get("worker_hash"))
    return bool(ai.get("provider") in ("anthropic", "openai") and ai.get("key") and ai.get("model"))


def set_ai(username: str, provider: str, model: str, key: str | None) -> dict:
    """key가 None이면 저장된 키를 유지한다(같은 제공자일 때만)."""
    if provider not in PROVIDERS:
        raise ValueError("지원하지 않는 AI입니다.")

    def f(u):
        old = u.get("ai") or {}
        ai = {"provider": provider, "model": (model or "").strip(), "updated_at": storage.now()}
        if provider == "worker" and not u.get("worker_hash"):
            raise ValueError("먼저 실행기 토큰을 발급하고 내 PC에서 실행기를 켜세요.")
        if provider in ("anthropic", "openai"):
            if key:
                ai["key"] = _fernet().encrypt(key.strip().encode()).decode()
                ai["key_hint"] = "…" + key.strip()[-4:]
            elif old.get("provider") == provider and old.get("key"):
                ai["key"], ai["key_hint"] = old["key"], old.get("key_hint", "")
            else:
                raise ValueError("API 키를 입력하세요.")
            if not ai["model"]:
                raise ValueError("모델을 고르세요.")
        u["ai"] = ai
    return _update(username, f)


def ai_config(u: dict | None) -> dict:
    """AI 호출용 설정 (복호화된 키 포함 — 서버 안에서만 사용)."""
    ai = dict((u or {}).get("ai") or {})
    if ai.get("key"):
        try:
            ai["key"] = _fernet().decrypt(ai["key"].encode()).decode()
        except InvalidToken:
            ai["key"] = ""
    return ai


# ---------------- 분석 실행기 토큰 ----------------
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_worker_token(username: str) -> str:
    """새 실행기 토큰 발급(이전 토큰은 무효). 원문은 이때 한 번만 돌려주고 해시만 저장한다."""
    token = "pw_" + secrets.token_urlsafe(32)

    def f(u):
        u["worker_hash"] = _token_hash(token)
        u["worker_issued_at"] = storage.now()
    _update(username, f)
    return token


def worker_user(token: str | None) -> dict | None:
    if not token:
        return None
    h = _token_hash(token)
    for name, u in _load()["users"].items():
        if u.get("worker_hash") and hmac.compare_digest(u["worker_hash"], h):
            return {**u, "username": name}
    return None


def new_password() -> str:
    return secrets.token_urlsafe(9)
