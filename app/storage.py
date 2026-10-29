"""프로젝트 데이터 저장소: data/projects/<id>/project.json + 업로드/결과 폴더."""
import json
import re
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
PROJECTS_DIR = DATA_DIR / "projects"
TEMPLATES_DIR = DATA_DIR / "templates"

FILE_KINDS = ("current_rfp", "notice", "past_rfp", "past_notice")  # notice = 현재 공고서

_lock = threading.Lock()


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def safe_name(name: str) -> str:
    name = Path(name).name
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    return name or "file"


def project_dir(pid: str) -> Path:
    if not re.fullmatch(r"[\w-]+", pid):
        raise ValueError("invalid project id")
    return PROJECTS_DIR / pid


def _empty_project(pid: str, name: str) -> dict:
    return {
        "id": pid,
        "name": name,
        "client": "",
        "deadline": "",
        "memo": "",
        "created_at": now(),
        "files": {k: [] for k in FILE_KINDS},
        "step1": None,
        "step2": None,
        "step3": {"template": None, "toc": [], "outputs": []},
    }


WORKSPACE_FIELDS = ("name", "client", "deadline", "memo")


def create_project(data: dict) -> dict:
    pid = datetime.now().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6]
    d = project_dir(pid)
    for sub in ("uploads", "output"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    p = _empty_project(pid, str(data.get("name", "")).strip() or "새 워크스페이스")
    for k in WORKSPACE_FIELDS[1:]:
        p[k] = str(data.get(k, "")).strip()
    save_project(p)
    return p


def d_day(deadline: str) -> str:
    """'2026-11-30' → 'D-32' / 'D-DAY' / '마감'."""
    try:
        days = (datetime.strptime(deadline[:10], "%Y-%m-%d").date() - datetime.now().date()).days
    except (ValueError, TypeError):
        return ""
    return "D-DAY" if days == 0 else f"D-{days}" if days > 0 else "마감"


def list_projects() -> list[dict]:
    if not PROJECTS_DIR.exists():
        return []
    items = []
    for f in PROJECTS_DIR.glob("*/project.json"):
        try:
            items.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(items, key=lambda p: p.get("created_at", ""), reverse=True)


def load_project(pid: str) -> dict | None:
    f = project_dir(pid) / "project.json"
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def save_project(p: dict) -> None:
    f = project_dir(p["id"]) / "project.json"
    with _lock:
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(f)


def delete_project(pid: str) -> None:
    d = project_dir(pid)
    if d.exists():
        shutil.rmtree(d)


def upload_path(pid: str, kind: str, filename: str) -> Path:
    d = project_dir(pid) / "uploads" / kind
    d.mkdir(parents=True, exist_ok=True)
    return d / safe_name(filename)


def output_dir(pid: str) -> Path:
    d = project_dir(pid) / "output"
    d.mkdir(parents=True, exist_ok=True)
    return d

