"""제안서 루틴 도우미 - FastAPI 서버."""
import re
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402

import threading  # noqa: E402
import time  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402

from . import analysis, auth, claude_client, company, storage, tpl_store  # noqa: E402
from .claude_client import ClaudeError  # noqa: E402
from .extractors import SUPPORTED, ExtractError  # noqa: E402
from .pptx_builder import SAMPLE_TOC, build_proposal_pptx  # noqa: E402
from .table_image import render_compare_table  # noqa: E402

APP_DIR = Path(__file__).resolve().parent
MAX_UPLOAD = 50 * 1024 * 1024
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

app = FastAPI(title="제안서 루틴 도우미")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")
templates.env.globals["d_day"] = storage.d_day

KIND_LABELS = {"current_rfp": "현재 RFP", "notice": "현재 공고서", "past_rfp": "과거 RFP", "past_notice": "과거 공고서"}


@app.middleware("http")
async def _no_stale_cache(request: Request, call_next):
    # 브라우저가 항상 최신 화면·CSS·JS를 확인하도록 한다 (변경 없으면 ETag로 304)
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-cache")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


# ---------------- 로그인·최초 설정 ----------------
# /api/worker/ 는 쿠키 대신 실행기 토큰(Authorization: Bearer)으로 인증한다
PUBLIC = ("/static/", "/login", "/setup", "/api/health", "/api/auth/login", "/api/setup", "/api/worker/")
# 로그인은 했지만 비밀번호 변경·AI 연결 설정 전에도 열 수 있는 경로
ONBOARDING = ("/welcome", "/api/me", "/api/auth/logout")
# AI가 있어야 하는 작업 (POST). 'AI 연결 안 함' 사용자는 화면에서 버튼이 꺼지고, 서버도 여기서 막는다
AI_PATHS = re.compile(r"^/api/(projects/[^/]+/(step1/analyze|strategy/generate|step2/generate|step3/toc)"
                      r"|templates/[^/]+/structure)$")
NO_AI_MSG = "AI가 연결되어 있지 않아 이 기능을 쓸 수 없습니다. 왼쪽 아래 '⚙ 내 AI 연결'에서 AI를 연결하세요."


def _is_local(request: Request) -> bool:
    """서버 PC에서 직접 접속했는지. 터널(cloudflared)·프록시를 거친 요청은 전달 헤더가 붙는다."""
    host = request.client.host if request.client else ""
    fwd = any(h in request.headers for h in ("cf-connecting-ip", "x-forwarded-for", "forwarded", "cf-ray"))
    return host in ("127.0.0.1", "::1") and not fwd


def _deny(request: Request, status: int, msg: str, to: str):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": msg}, status_code=status)
    return RedirectResponse(to, status_code=303)


@app.middleware("http")
async def _auth(request: Request, call_next):
    path = request.url.path
    local = _is_local(request)
    request.state.local = local
    request.state.user = user = auth.read_session(request.cookies.get(auth.COOKIE))
    if user:
        claude_client.use({**auth.ai_config(user), "local": local, "username": user["username"]})
    if not path.startswith(PUBLIC):
        if not auth.has_users():
            return _deny(request, 401, "최초 설정이 필요합니다.", "/setup")
        if not user:
            return _deny(request, 401, "로그인이 필요합니다.", "/login")
        if not path.startswith(ONBOARDING) and (user.get("must_change_pw") or not auth.ai_ready(user)):
            return _deny(request, 403, "먼저 내 계정·AI 연결 설정을 마치세요.", "/welcome")
        if path.startswith(("/users", "/api/users")) and user.get("role") != "admin":
            return _deny(request, 403, "관리자만 사용할 수 있습니다.", "/")
        if request.method == "POST" and AI_PATHS.match(path) and not auth.ai_enabled(user):
            return JSONResponse({"detail": NO_AI_MSG}, status_code=400)
    return await call_next(request)


def _set_cookie(request: Request, resp, u: dict) -> None:
    https = request.headers.get("x-forwarded-proto") == "https" or "cf-visitor" in request.headers
    resp.set_cookie(auth.COOKIE, auth.make_session(u), max_age=auth.SESSION_DAYS * 86400,
                    httponly=True, samesite="lax", secure=https)


def _me(request: Request) -> dict:
    return request.state.user


@app.get("/api/health")
def health():
    return {"ok": True, "users": auth.has_users()}


@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    if auth.has_users():
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"mode": "setup", "local": request.state.local})


@app.post("/api/setup")
def do_setup(request: Request, data: dict = Body(...)):
    if auth.has_users():
        raise HTTPException(400, "이미 설정이 끝났습니다.")
    if not request.state.local:
        raise HTTPException(403, "최초 관리자 계정은 서버 PC에서 직접 접속해 만들어야 합니다.")
    try:
        u = auth.create(data.get("username", ""), data.get("password", ""), data.get("name", ""), role="admin")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    resp = JSONResponse({"ok": True, "next": "/welcome"})
    _set_cookie(request, resp, u)
    return resp


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if not auth.has_users():
        return RedirectResponse("/setup", status_code=303)
    if request.state.user:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"mode": "login", "local": request.state.local})


@app.post("/api/auth/login")
def do_login(request: Request, data: dict = Body(...)):
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "")
    try:
        u = auth.login(data.get("username", ""), data.get("password", ""), ip)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    nxt = "/welcome" if u.get("must_change_pw") or not auth.ai_ready(u) else "/"
    resp = JSONResponse({"ok": True, "next": nxt})
    _set_cookie(request, resp, u)
    return resp


@app.post("/api/auth/logout")
def do_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    return resp


@app.get("/welcome", response_class=HTMLResponse)
def welcome_page(request: Request):
    return templates.TemplateResponse(request, "welcome.html", {
        "me": auth.public(_me(request)), "local": request.state.local, "providers": auth.PROVIDERS,
        "cli": claude_client.cli_status() if request.state.local else {}})


# ---------------- 내 계정 · 내 AI 연결 ----------------
@app.get("/api/me")
def get_me(request: Request):
    return auth.public(_me(request))


@app.put("/api/me/password")
def change_password(request: Request, data: dict = Body(...)):
    u = _me(request)
    try:
        auth.login(u["username"], data.get("current", ""), "pw:" + u["username"])
        u = auth.set_password(u["username"], data.get("new", ""))
    except ValueError as e:
        raise HTTPException(400, str(e).replace("아이디 또는 비밀번호", "현재 비밀번호")) from e
    resp = JSONResponse(auth.public(u))
    _set_cookie(request, resp, u)  # 비밀번호가 바뀌면 세션을 새로 발급한다
    return resp


def _check_provider(request: Request, provider: str) -> None:
    if provider == "cli" and not request.state.local:
        raise HTTPException(400, "'이 PC의 Pro 계정'은 서버 PC에서 직접 접속할 때만 고를 수 있습니다.")


@app.post("/api/me/ai/models")
def ai_models(request: Request, data: dict = Body(...)):
    """입력한 키(없으면 저장된 키)로 모델 목록을 가져온다 — 키 확인을 겸한다."""
    provider = data.get("provider", "")
    _check_provider(request, provider)
    key = (data.get("key") or "").strip()
    if not key:
        cfg = auth.ai_config(_me(request))
        key = cfg.get("key", "") if cfg.get("provider") == provider else ""
    if not key:
        raise HTTPException(400, "API 키를 입력하세요.")
    return {"models": claude_client.list_models(provider, key)}


@app.put("/api/me/ai")
def save_ai(request: Request, data: dict = Body(...)):
    provider = data.get("provider", "")
    _check_provider(request, provider)
    try:
        u = auth.set_ai(_me(request)["username"], provider, data.get("model", ""), (data.get("key") or "").strip() or None)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return auth.public(u)


@app.post("/api/me/ai/test")
def test_connection(request: Request):
    schema = {"type": "object", "properties": {"reply": {"type": "string"}},
              "required": ["reply"], "additionalProperties": False}
    me = auth.get(_me(request)["username"])
    claude_client.use({**auth.ai_config(me), "local": request.state.local, "username": me["username"]})
    t = time.time()
    out = claude_client.call_json("짧게 답하세요.", [{"type": "text", "text": "연결 테스트입니다. '연결 성공'이라고만 답하세요."}],
                                  schema, max_tokens=1000, light=True)
    return {"ok": True, "provider": claude_client.active_backend(), "seconds": round(time.time() - t, 1),
            "reply": out.get("reply", "")}


# ---------------- 내 PC의 분석 실행기 ----------------
@app.get("/api/me/worker")
def my_worker(request: Request):
    u = _me(request)
    return {"online": claude_client.worker_online(u["username"]), "token": bool(u.get("worker_hash")),
            "issued_at": u.get("worker_issued_at", "")}


@app.post("/api/me/worker/token")
def my_worker_token(request: Request):
    return {"token": auth.issue_worker_token(_me(request)["username"])}


@app.get("/api/me/worker/script")
def my_worker_script():
    return FileResponse(APP_DIR / "cli_runner.py", media_type="text/x-python", filename="proposal-worker.py")


@app.get("/api/me/worker/installer")
def my_worker_installer(request: Request):
    """Windows 원클릭 설치 파일 — 서버 주소와 새 실행기 토큰을 넣어 내려준다(이전 토큰은 끊긴다).
    Python·Claude Code 자동 설치, 최초 1회 Claude 로그인, 시작 프로그램 등록(콘솔 창 없이 pythonw)."""
    server = str(request.base_url).rstrip("/")
    token = auth.issue_worker_token(_me(request)["username"])
    if "'" in server:
        raise HTTPException(400, "서버 주소를 확인할 수 없습니다.")
    text = (APP_DIR / "worker_setup.cmd").read_text(encoding="utf-8")
    text = text.replace("__SERVER__", server).replace("__TOKEN__", token)
    body = text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")  # cmd는 CRLF, BOM 없이
    return Response(body, media_type="application/octet-stream", headers={
        "Content-Disposition": 'attachment; filename="proposal-worker-setup.cmd"', "Cache-Control": "no-store"})


@app.get("/api/worker/hello")
def worker_hello(request: Request):
    """실행기 시작 시 토큰·연결을 바로 확인하는 용도 (long-poll 전에 '[연결됨]'을 표시)."""
    return {"ok": True, "username": _worker(request)["username"]}


@app.get("/api/worker/script")
def worker_script(request: Request):
    """설치 파일이 실행기 스크립트를 받아 가는 경로 (실행기 토큰 인증)."""
    _worker(request)
    return FileResponse(APP_DIR / "cli_runner.py", media_type="text/x-python", filename="proposal-worker.py")


def _worker(request: Request) -> dict:
    h = request.headers.get("authorization", "")
    u = auth.worker_user(h[7:].strip() if h.lower().startswith("bearer ") else "")
    if not u:
        raise HTTPException(401, "실행기 토큰이 올바르지 않습니다.")
    return u


@app.get("/api/worker/poll")
def worker_poll(request: Request):
    """실행기의 long-poll — 내 분석 작업이 생기면 돌려주고, 없으면 25초 뒤 204."""
    u = _worker(request)
    job = claude_client.worker_take(u["username"], wait=25)
    if job is None:
        return Response(status_code=204)
    return job


@app.post("/api/worker/result/{job_id}")
def worker_result(request: Request, job_id: str, data: dict = Body(...)):
    u = _worker(request)
    if not claude_client.worker_finish(u["username"], job_id, data):
        raise HTTPException(404, "작업을 찾을 수 없습니다(이미 시간 초과됐을 수 있음).")
    return {"ok": True}


# ---------------- 사용자 관리 (관리자) ----------------
@app.get("/users", response_class=HTMLResponse)
def users_page(request: Request):
    return _render(request, "users.html", active="users", users=auth.list_users())


@app.post("/api/users")
def add_user(data: dict = Body(...)):
    pw = auth.new_password()
    try:
        u = auth.create(data.get("username", ""), pw, data.get("name", ""),
                        role="admin" if data.get("role") == "admin" else "member", must_change_pw=True)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"user": auth.public(u), "password": pw}


@app.post("/api/users/{username}/reset")
def reset_user(username: str):
    pw = auth.new_password()
    try:
        auth.set_password(username, pw, must_change=True)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"password": pw}


@app.put("/api/users/{username}")
def update_user(username: str, data: dict = Body(...)):
    try:
        return auth.public(auth.set_role(username, data.get("role", "")))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.delete("/api/users/{username}")
def remove_user(request: Request, username: str):
    if username == _me(request)["username"]:
        raise HTTPException(400, "본인 계정은 삭제할 수 없습니다.")
    try:
        auth.delete(username)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@app.on_event("startup")
def _startup() -> None:
    storage.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    tpl_store.init()


@app.exception_handler(ClaudeError)
@app.exception_handler(ExtractError)
async def _known_error(_: Request, exc: Exception):
    return JSONResponse({"detail": str(exc)}, status_code=400)


def _project(pid: str) -> dict:
    try:
        p = storage.load_project(pid)
    except ValueError:
        p = None
    if p is None:
        raise HTTPException(404, "워크스페이스를 찾을 수 없습니다.")
    for k in storage.FILE_KINDS:  # 이전 버전 데이터 호환
        p["files"].setdefault(k, [])
    return p


def _files(p: dict, kind: str) -> list[Path]:
    return [storage.upload_path(p["id"], kind, n) for n in p["files"].get(kind, [])]


def _render(request: Request, name: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(
        request, name, {"kind_labels": KIND_LABELS, "workspaces": storage.list_projects(),
                        "me": auth.public(request.state.user), "local": request.state.local,
                        "ai_on": auth.ai_enabled(request.state.user),
                        "analyzing": {pid for pid in list(_analyze_jobs) if _analyze_running(pid)}, **ctx}
    )


# ---------------- 페이지 ----------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return _render(request, "index.html", active="home")


@app.get("/templates", response_class=HTMLResponse)
def templates_page(request: Request):
    return _render(request, "templates.html", active="templates", tpls=tpl_store.list_templates())


@app.get("/company", response_class=HTMLResponse)
def company_page(request: Request):
    return _render(request, "company.html", active="company", companies=company.all_companies(),
                   kinds=company.KINDS, max_chars=company.MAX_CHARS)


@app.get("/api/companies")
def list_companies():
    return company.all_companies()


@app.post("/api/companies")
def add_company(data: dict = Body(...)):
    try:
        return company.create(str(data.get("name", "")), str(data.get("kind", "partner")))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.put("/api/companies/{cid}")
def put_company(cid: str, data: dict = Body(...)):
    try:
        return company.update(cid, str(data.get("name", "")), str(data.get("kind", "")), str(data.get("profile", "")))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.delete("/api/companies/{cid}")
def del_company(cid: str):
    try:
        company.delete(cid)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return _render(request, "settings.html", active="settings", providers=auth.PROVIDERS,
                   cli=claude_client.cli_status() if request.state.local else {})


@app.get("/workspaces/{pid}", response_class=HTMLResponse)
def workspace_home(request: Request, pid: str):
    p = _project(pid)
    companies = [{"id": c["id"], "name": c["name"], "kind": c["kind"], "has_profile": bool(c.get("profile", "").strip())}
                 for c in company.all_companies()]
    return _render(request, "workspace.html", p=p, active="home", companies=companies,
                   kinds=company.KINDS, **_consortium_ctx(p))


@app.get("/workspaces/{pid}/step1", response_class=HTMLResponse)
def step1_page(request: Request, pid: str):
    return _render(request, "step1.html", p=_project(pid), active="step1", analyze=_analyze_state(pid))


@app.get("/workspaces/{pid}/step2", response_class=HTMLResponse)
def step2_page(request: Request, pid: str):
    return _render(request, "step2.html", p=_project(pid), active="step2", categories=analysis.CATEGORIES)


@app.get("/workspaces/{pid}/checklist")
def checklist_page(pid: str):
    # 공고서 분석·체크리스트는 2단계 한 페이지로 합쳐졌다 (예전 주소 호환)
    return RedirectResponse(f"/workspaces/{pid}/step2", status_code=307)


@app.get("/workspaces/{pid}/strategy", response_class=HTMLResponse)
def strategy_page(request: Request, pid: str):
    return _render(request, "strategy.html", p=_project(pid), active="strategy",
                   strategy_running=_strategy_running(pid), **_consortium_ctx(_project(pid)))


@app.get("/workspaces/{pid}/scoring", response_class=HTMLResponse)
def scoring_page(request: Request, pid: str):
    """평가 배점표 — 3단계 전략 추천 때 함께 정리된 strategy.scoring을 따로 보여 준다."""
    return _render(request, "scoring.html", p=_project(pid), active="scoring",
                   strategy_running=_strategy_running(pid))


@app.get("/workspaces/{pid}/step3", response_class=HTMLResponse)
def step3_page(request: Request, pid: str):
    return _render(request, "step3.html", p=_project(pid), active="step3", tpls=tpl_store.list_templates())


# ---------------- 워크스페이스 / 파일 ----------------
@app.post("/api/projects")
def create_project(data: dict = Body(...)):
    return storage.create_project(data)


@app.put("/api/projects/{pid}")
def update_project(pid: str, data: dict = Body(...)):
    p = _project(pid)
    for k in storage.WORKSPACE_FIELDS:
        if k in data:
            p[k] = str(data[k]).strip()
    if "companies" in data:
        known = {c["id"] for c in company.all_companies()}
        ids = [str(i) for i in (data["companies"] or []) if str(i) in known]
        ids = list(dict.fromkeys(ids))  # 중복 제거(순서 유지)
        if not ids:
            raise HTTPException(400, "제안에 참여할 회사를 1개 이상 고르세요.")
        p["companies"] = ids
    if not p["name"]:
        raise HTTPException(400, "워크스페이스 이름을 입력하세요.")
    storage.save_project(p)
    return p


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    _project(pid)
    storage.delete_project(pid)
    return {"ok": True}


@app.post("/api/projects/{pid}/files/{kind}")
async def upload_files(pid: str, kind: str, files: list[UploadFile] = File(...)):
    p = _project(pid)
    if kind not in storage.FILE_KINDS:
        raise HTTPException(400, "잘못된 파일 종류입니다.")
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in SUPPORTED:
            raise HTTPException(400, f"지원하지 않는 형식입니다: {f.filename} (지원: {', '.join(sorted(SUPPORTED))})")
        data = await f.read()
        if len(data) > MAX_UPLOAD:
            raise HTTPException(400, f"{f.filename}: 50MB를 초과합니다.")
        dest = storage.upload_path(pid, kind, f.filename)
        dest.write_bytes(data)
        if dest.name not in p["files"][kind]:
            p["files"][kind].append(dest.name)
    storage.save_project(p)
    return p["files"]


@app.post("/api/projects/{pid}/files/{kind}/{name}/move")
def move_file(pid: str, kind: str, name: str, data: dict = Body(...)):
    p = _project(pid)
    to = data.get("to")
    if kind not in storage.FILE_KINDS or to not in storage.FILE_KINDS or name not in p["files"][kind]:
        raise HTTPException(400, "잘못된 요청입니다.")
    if to != kind:
        if name in p["files"][to]:
            raise HTTPException(400, f"'{KIND_LABELS[to]}'에 같은 이름의 파일이 이미 있습니다.")
        storage.upload_path(pid, kind, name).replace(storage.upload_path(pid, to, name))
        p["files"][kind].remove(name)
        if name not in p["files"][to]:
            p["files"][to].append(name)
        storage.save_project(p)
    return p["files"]


@app.delete("/api/projects/{pid}/files/{kind}/{name}")
def delete_file(pid: str, kind: str, name: str):
    p = _project(pid)
    if kind not in storage.FILE_KINDS or name not in p["files"].get(kind, []):
        raise HTTPException(404, "파일이 없습니다.")
    storage.upload_path(pid, kind, name).unlink(missing_ok=True)
    p["files"][kind].remove(name)
    storage.save_project(p)
    return p["files"]


# ---------------- 단계 1 (백그라운드 작업 — 페이지를 떠나도 계속 진행) ----------------
_analyze_jobs: dict = {}  # pid -> {"future", "started_at", "notice"}


def _analyze_running(pid: str) -> bool:
    j = _analyze_jobs.get(pid)
    return bool(j and not j["future"].done())


def _analyze_state(pid: str) -> dict:
    j = _analyze_jobs.get(pid)
    if not j:
        return {"running": False}
    f = j["future"]
    st = {"running": not f.done(), "started_at": j["started_at"], "notice": j["notice"]}
    if f.done():
        e = f.exception()
        if e is None:
            st["result"] = f.result()
        elif isinstance(e, HTTPException):
            st["error"] = e.detail
        elif isinstance(e, (ClaudeError, ExtractError, ValueError)):
            st["error"] = str(e)
        else:
            st["error"] = f"분석 중 오류가 발생했습니다: {e}"
    return st


@app.post("/api/projects/{pid}/step1/analyze")
def step1_analyze(pid: str):
    p = _project(pid)
    docs = {k: _files(p, k) for k in storage.FILE_KINDS}
    if not docs["current_rfp"] and not docs["notice"]:
        raise HTTPException(400, "현재 RFP 또는 현재 공고서를 먼저 업로드하세요.")
    with _jobs_lock:
        if not _analyze_running(pid):
            _analyze_jobs[pid] = {"future": _bg.submit(claude_client.bound(_run_analyze), pid), "started_at": time.time(),
                                  "notice": bool(docs["notice"])}
    return _analyze_state(pid)


@app.get("/api/projects/{pid}/step1/status")
def step1_status(pid: str):
    _project(pid)
    return _analyze_state(pid)


@app.get("/api/jobs")
def running_jobs():
    """사이드바·알림용: 진행 중인 분석 작업 목록."""
    out = []
    for pid in list(_analyze_jobs):
        if _analyze_running(pid):
            out.append({"pid": pid, "kind": "analyze"})
    for pid in list(_strategy_jobs):
        if _strategy_running(pid):
            out.append({"pid": pid, "kind": "strategy"})
    names = {w["id"]: w["name"] for w in storage.list_projects()}
    return [{**j, "name": names.get(j["pid"], "")} for j in out]


def _run_analyze(pid: str) -> dict:
    p = _project(pid)
    docs = {k: _files(p, k) for k in storage.FILE_KINDS}
    # 핵심 추진 전략은 백그라운드에서 따로 만든다 (3단계 페이지에서 결과를 받음)
    _strategy_job(pid, force=True)
    # 현재 공고서가 있으면 공고서 분석·체크리스트도 동시에 생성한다
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_compare = pool.submit(claude_client.bound(analysis.analyze_rfp), docs["current_rfp"], docs["notice"],
                                docs["past_rfp"], docs["past_notice"])
        f_check = pool.submit(claude_client.bound(analysis.build_checklist), docs["notice"]) if docs["notice"] else None
        result = f_compare.result()
        check, check_error = None, ""
        if f_check is not None:
            try:
                check = f_check.result()
            except (ClaudeError, ExtractError) as e:
                check_error = str(e)
    result["analyzed_at"] = storage.now()
    result["compared"] = bool(docs["past_rfp"] or docs["past_notice"])
    result["documents"] = {k: [d.name for d in v] for k, v in docs.items()}
    p = _project(pid)  # 그사이 저장된 변경(추진 전략 등)을 덮어쓰지 않도록 다시 읽는다
    p["step1"] = result
    if check is not None:
        _apply_checklist(p, check, docs["notice"])
    storage.save_project(p)
    render_compare_table(result, storage.output_dir(pid) / "rfp_compare.png",
                         f"RFP 비교 분석 - {result.get('project_title') or p['name']}")
    return {**result, "checklist": {
        "ran": f_check is not None, "ok": check is not None, "error": check_error,
        "count": len(p["step2"]["items"]) if check is not None else 0},
        "strategy": {"running": _strategy_running(pid)}}


# ---------------- 단계 3: 핵심 추진 전략 (백그라운드 작업) ----------------
_bg = ThreadPoolExecutor(max_workers=4)
_strategy_jobs: dict = {}
_jobs_lock = threading.Lock()


def _strategy_running(pid: str) -> bool:
    f = _strategy_jobs.get(pid)
    return bool(f and not f.done())


def _strategy_job(pid: str, force: bool = False):
    """진행 중인 작업이 있으면 그것을, 없으면 새로 시작한 작업(Future)을 돌려준다."""
    with _jobs_lock:
        f = _strategy_jobs.get(pid)
        if f and not f.done():
            return f
        if not force and _project(pid).get("strategy"):
            return None
        f = _bg.submit(claude_client.bound(_run_strategy), pid)
        _strategy_jobs[pid] = f
        return f


def _consortium_ctx(p: dict) -> dict:
    """화면용: 이 워크스페이스의 참여 회사(첫 번째 = 주관사)와 현재 현황 저장본 서명."""
    members = company.resolve(p.get("companies"))
    return {"members": [{"id": c["id"], "name": c["name"], "kind": c["kind"], "has_profile": bool(c.get("profile", "").strip()),
                         "updated_at": c.get("updated_at", "")} for c in members],
            "company_sig": company.signature(members)}


def _strategy_context(p: dict) -> str:
    parts = []
    s1 = p.get("step1") or {}
    if s1:
        parts.append("사업 요약: " + s1.get("summary", ""))
        parts += ["핵심 변경점: " + x for x in s1.get("key_changes", [])]
    ov = (p.get("step2") or {}).get("overview") or {}
    if ov:
        parts.append(f"입찰방식: {ov.get('bid_method', '')} / 공동수급: {ov.get('joint_bid', '')}")
        parts += ["참가자격: " + x for x in ov.get("qualifications", [])]
    return "\n".join(parts)


def _run_strategy(pid: str) -> dict:
    p = _project(pid)
    docs = {k: _files(p, k) for k in storage.FILE_KINDS}
    if not docs["current_rfp"] and not docs["notice"]:
        raise ValueError("현재 RFP 또는 현재 공고서를 먼저 업로드하세요(1단계).")
    members = company.resolve(p.get("companies"))
    profile = company.prompt_block(members)
    result = analysis.build_strategy(docs["current_rfp"], docs["notice"], docs["past_rfp"],
                                     docs["past_notice"], _strategy_context(p), profile)
    result["generated_at"] = storage.now()
    result["company_applied"] = company.signature(members) if profile else ""
    result["company_names"] = [c["name"] for c in members] if profile else []
    result["documents"] = [d.name for v in docs.values() for d in v]
    p = _project(pid)
    p["strategy"] = result
    storage.save_project(p)
    return result


@app.post("/api/projects/{pid}/strategy/generate")
def strategy_generate(pid: str, data: dict = Body(default={})):
    _project(pid)
    job = _strategy_job(pid, force=bool(data.get("force")))
    if job is None:
        return _project(pid)["strategy"]
    try:
        return job.result()
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/projects/{pid}/strategy/status")
def strategy_status(pid: str):
    p = _project(pid)
    return {"running": _strategy_running(pid), "has_result": bool(p.get("strategy"))}


@app.get("/api/projects/{pid}/step1/table.png")
def step1_table(pid: str):
    p = _project(pid)
    img = storage.output_dir(pid) / "rfp_compare.png"
    if not p.get("step1"):
        raise HTTPException(404, "분석 결과가 없습니다.")
    if not img.exists():
        render_compare_table(p["step1"], img, f"RFP 비교 분석 - {p['step1'].get('project_title') or p['name']}")
    return FileResponse(img, media_type="image/png", filename=f"{p['name']}_RFP비교표.png",
                        content_disposition_type="inline")


# ---------------- 단계 2 ----------------
@app.post("/api/projects/{pid}/step2/generate")
def step2_generate(pid: str):
    p = _project(pid)
    docs = _files(p, "notice") or _files(p, "current_rfp")
    if not docs:
        raise HTTPException(400, "공고서 또는 현재 RFP를 먼저 업로드하세요.")
    _apply_checklist(p, analysis.build_checklist(docs), docs)
    storage.save_project(p)
    return p["step2"]


def _apply_checklist(p: dict, result: dict, docs: list[Path]) -> None:
    """공고서 분석 결과를 반영. 같은 서류명의 체크·메모와 직접 추가한 항목은 유지한다."""
    prev = {i["name"]: i for i in (p.get("step2") or {}).get("items", [])}
    items = []
    for it in result["items"]:
        old = prev.get(it["name"], {})
        items.append({**it, "id": uuid.uuid4().hex[:8], "checked": old.get("checked", False),
                      "memo": old.get("memo", "")})
    items += [i for i in prev.values() if i.get("custom")]
    p["step2"] = {"overview": result.get("overview"), "schedule": result["schedule"],
                  "submission": result["submission"], "items": items, "generated_at": storage.now(),
                  "source": [d.name for d in docs]}


@app.put("/api/projects/{pid}/checklist")
def save_checklist(pid: str, data: dict = Body(...)):
    p = _project(pid)
    if not p.get("step2"):
        p["step2"] = {"schedule": [], "submission": "", "items": []}
    items = data.get("items")
    if not isinstance(items, list):
        raise HTTPException(400, "items가 필요합니다.")
    p["step2"]["items"] = items
    storage.save_project(p)
    return {"ok": True}


# ---------------- 템플릿 관리 ----------------
def _tpl_or_404(file: str) -> dict:
    t = tpl_store.get(file)
    if t is None:
        raise HTTPException(404, "템플릿을 찾을 수 없습니다.")
    return t


@app.get("/api/templates")
def get_templates():
    return tpl_store.list_templates()


@app.post("/api/templates")
def upload_template(file: UploadFile = File(...), name: str = Form(""), description: str = Form(""),
                    structure: bool = Form(False)):
    data = file.file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(400, "50MB를 초과합니다.")
    try:
        t = tpl_store.add(file.filename or "template.pptx", data, name, description)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if structure and claude_client.active_backend() in ("", "none"):
        return {**t, "structure_error": NO_AI_MSG}
    if structure and (t.get("form") or {}).get("kind") != "token":  # 이미 토큰형 양식이면 그대로 둔다
        try:
            t = tpl_store.structure(t["file"])
        except (ClaudeError, ValueError) as e:
            t = {**tpl_store.get(t["file"]), "structure_error": str(e)}
    return t


@app.post("/api/templates/{file}/structure")
def structure_template(file: str):
    _tpl_or_404(file)
    try:
        return tpl_store.structure(file)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/templates/{file}/original")
def template_original(file: str):
    _tpl_or_404(file)
    f = tpl_store.original_path(file)
    if not f.exists():
        raise HTTPException(404, "보관된 원본이 없습니다.")
    return FileResponse(f, filename=f"원본_{file}", media_type=PPTX_MIME)


@app.get("/api/templates/{file}/preview/{n}")
def template_preview(file: str, n: int):
    f = tpl_store.preview_dir(file) / f"s{n:02d}.png"
    if not f.exists():
        raise HTTPException(404, "미리보기가 없습니다.")
    return FileResponse(f, media_type="image/png")


@app.put("/api/templates/{file}")
def update_template(file: str, data: dict = Body(...)):
    _tpl_or_404(file)
    return tpl_store.update(file, data)


@app.delete("/api/templates/{file}")
def delete_template(file: str):
    _tpl_or_404(file)
    try:
        tpl_store.delete(file)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@app.post("/api/templates/{file}/describe")
def describe_template(file: str):
    _tpl_or_404(file)
    return tpl_store.redescribe(file)


@app.get("/api/templates/{file}/download")
def download_template(file: str):
    t = _tpl_or_404(file)
    name = f"{Path(file).stem}_양식.pptx" if t.get("structured") else file
    return FileResponse(tpl_store.path(file), filename=name, media_type=PPTX_MIME)


@app.post("/api/templates/{file}/sample")
def sample_template(file: str):
    t = _tpl_or_404(file)
    out = storage.DATA_DIR / "samples" / f"샘플_{Path(file).stem}.pptx"
    build_proposal_pptx(tpl_store.path(file), SAMPLE_TOC, "샘플 사업명\n세부 과정명", "",
                        out, t.get("mapping"))
    return FileResponse(out, filename=out.name, media_type=PPTX_MIME)


# ---------------- 단계 3 ----------------
@app.post("/api/projects/{pid}/step3/toc")
def step3_toc(pid: str):
    p = _project(pid)
    docs = _files(p, "current_rfp")
    if not docs:
        raise HTTPException(400, "현재 RFP를 먼저 업로드하세요(단계 1).")
    result = analysis.extract_toc(docs)
    p["step3"].update({"toc": result["toc"], "proposal_title": result["proposal_title"],
                       "toc_at": storage.now()})
    storage.save_project(p)
    return p["step3"]


@app.put("/api/projects/{pid}/step3/toc")
def step3_save(pid: str, data: dict = Body(...)):
    p = _project(pid)
    for key in ("toc", "proposal_title", "template", "company"):
        if key in data:
            p["step3"][key] = data[key]
    storage.save_project(p)
    return p["step3"]


@app.post("/api/projects/{pid}/step3/build")
def step3_build(pid: str, data: dict = Body(default={})):
    p = _project(pid)
    s3 = p["step3"]
    for key in ("toc", "proposal_title", "template", "company"):
        if key in data:
            s3[key] = data[key]
    if not s3.get("toc"):
        raise HTTPException(400, "목차가 비어 있습니다. 목차를 추출하거나 직접 입력하세요.")
    t = tpl_store.get(s3.get("template") or "") or tpl_store.get(tpl_store.default_file() or "")
    if t is None:
        raise HTTPException(400, "사용할 템플릿이 없습니다. 템플릿 관리에서 추가하세요.")
    fname = storage.safe_name(f"{p['name']}_제안서목차_{storage.now().replace(':', '').replace(' ', '_')}.pptx")
    # 표지 날짜(YYYY.MM): 제출 마감일이 있으면 그 달, 없으면 이번 달
    ym = p.get("deadline", "")[:7].replace("-", ".") or None
    build_proposal_pptx(tpl_store.path(t["file"]), s3["toc"], s3.get("proposal_title", "") or p["name"],
                        s3.get("company", ""), storage.output_dir(pid) / fname, t.get("mapping"), ym)
    s3["template"] = t["file"]
    s3.setdefault("outputs", []).insert(0, fname)
    storage.save_project(p)
    return {"file": fname, "outputs": s3["outputs"]}


@app.get("/api/projects/{pid}/output/{name}")
def download_output(pid: str, name: str):
    _project(pid)
    f = storage.output_dir(pid) / storage.safe_name(name)
    if not f.exists():
        raise HTTPException(404, "파일이 없습니다.")
    return FileResponse(f, filename=f.name, media_type=PPTX_MIME if f.suffix == ".pptx" else None)
