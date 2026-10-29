"""PPT 템플릿 라이브러리: data/templates/*.pptx + templates.json(이름·설명·매핑·기본 지정)."""
import json
import shutil
import threading
from pathlib import Path

from . import storage
from . import template_analyze
from .pptx_builder import DEFAULT_TEMPLATE_NAME, ensure_default_template, layout_info
from .pptx_form import describe_form
from .pptx_token import describe_token_form

META_FILE = "templates.json"
ROLES = ("title", "section", "content")

_lock = threading.Lock()


def _dir() -> Path:
    return storage.TEMPLATES_DIR


def _load_meta() -> dict:
    f = _dir() / META_FILE
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def _save_meta(meta: dict) -> None:
    f = _dir() / META_FILE
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)


def init() -> None:
    _dir().mkdir(parents=True, exist_ok=True)
    ensure_default_template(_dir())
    list_templates()  # 메타데이터 동기화


def list_templates() -> list[dict]:
    """폴더의 .pptx와 메타데이터를 동기화한 뒤 목록 반환 (기본 템플릿 먼저)."""
    with _lock:
        meta = _load_meta()
        files = {f.name: f for f in _dir().glob("*.pptx") if not f.name.startswith("~$")}
        changed = False
        for name in list(meta):
            if name not in files:
                del meta[name]
                changed = True
        for name, f in files.items():
            m = meta.get(name)
            mtime = f.stat().st_mtime
            if m is None or m.get("mtime") != mtime or "description_source" not in m or "form" not in m:
                try:
                    info = layout_info(f)
                    # 양식형이면 역할 인식 결과, 아니면 None (토큰형 우선)
                    info["form"] = describe_token_form(f) or describe_form(f)
                except Exception as e:  # 손상된 파일
                    info = {"slide_size": "-", "layouts": [], "auto_mapping": {}, "slide_count": 0,
                            "form": None, "error": f"열 수 없는 파일: {e}"}
                m = {
                    "name": Path(name).stem,
                    "description": "",
                    "uploaded_at": storage.now(),
                    "mapping": {},
                    **(m or {}),
                    **info,
                    "mtime": mtime,
                    "size": f.stat().st_size,
                }
                if not m.get("description") and "description_source" not in m and not info.get("error"):
                    # 폴더에 직접 넣은 파일·기본 템플릿: 빠른 규칙 기반 설명
                    m["description"] = template_analyze.rule_description(template_analyze.extract_features(f))
                    m["description_source"] = "rule"
                m.setdefault("description_source", "user")
                meta[name] = m
                changed = True
        if not any(m.get("default") for m in meta.values()):
            first = DEFAULT_TEMPLATE_NAME if DEFAULT_TEMPLATE_NAME in meta else next(iter(sorted(meta)), None)
            if first:
                meta[first]["default"] = True
                changed = True
        if changed:
            _save_meta(meta)
    items = [{"file": k, "builtin": k == DEFAULT_TEMPLATE_NAME, "has_original": original_path(k).exists(), **v}
             for k, v in meta.items()]
    return sorted(items, key=lambda t: (not t.get("default"), not t["builtin"], t.get("uploaded_at", "")))


def get(file: str) -> dict | None:
    return next((t for t in list_templates() if t["file"] == file), None)


def default_file() -> str | None:
    items = list_templates()
    return items[0]["file"] if items else None


def path(file: str) -> Path:
    return _dir() / storage.safe_name(file)


def add(filename: str, data: bytes, name: str = "", description: str = "") -> dict:
    fname = storage.safe_name(filename)
    if not fname.lower().endswith(".pptx"):
        raise ValueError(".pptx 파일만 등록할 수 있습니다.")
    if fname == DEFAULT_TEMPLATE_NAME:
        raise ValueError("시스템 기본 템플릿과 같은 파일명은 사용할 수 없습니다.")
    dest = _dir() / fname
    dest.write_bytes(data)
    original_path(fname).unlink(missing_ok=True)  # 같은 이름으로 다시 올리면 이전 구조화 기록은 버린다
    shutil.rmtree(preview_dir(fname), ignore_errors=True)
    try:
        layout_info(dest)
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise ValueError(f"PowerPoint 파일을 열 수 없습니다: {e}") from e
    description = description.strip()
    source = "user"
    if not description:  # 비워두면 자동 분석
        description, source = template_analyze.describe(dest)
    with _lock:
        meta = _load_meta()
        old = meta.pop(fname, {})
        meta[fname] = {"name": name.strip() or Path(fname).stem, "description": description,
                       "description_source": source, "uploaded_at": storage.now(), "mapping": {},
                       "default": old.get("default", False), "structured": None}
        _save_meta(meta)
    return get(fname)


def redescribe(file: str) -> dict:
    """템플릿을 다시 분석해 설명을 새로 만든다."""
    description, source = template_analyze.describe(path(file))
    with _lock:
        meta = _load_meta()
        if file not in meta:
            raise KeyError(file)
        meta[file]["description"] = description
        meta[file]["description_source"] = source
        _save_meta(meta)
    return get(file)


def update(file: str, data: dict) -> dict:
    with _lock:
        meta = _load_meta()
        if file not in meta:
            raise KeyError(file)
        m = meta[file]
        if "name" in data and str(data["name"]).strip():
            m["name"] = str(data["name"]).strip()
        if "description" in data and str(data["description"]).strip() != m.get("description", ""):
            m["description"] = str(data["description"]).strip()
            m["description_source"] = "user"
        if "mapping" in data and isinstance(data["mapping"], dict):
            m["mapping"] = {r: v for r, v in data["mapping"].items() if r in ROLES and v in m.get("layouts", [])}
        if data.get("default"):
            for v in meta.values():
                v["default"] = False
            m["default"] = True
        _save_meta(meta)
    return get(file)


def delete(file: str) -> None:
    if file == DEFAULT_TEMPLATE_NAME:
        raise ValueError("시스템 기본 템플릿은 삭제할 수 없습니다.")
    path(file).unlink(missing_ok=True)
    original_path(file).unlink(missing_ok=True)
    shutil.rmtree(preview_dir(file), ignore_errors=True)
    list_templates()


# ---------------- 양식 자동 구조화 ----------------
def original_path(file: str) -> Path:
    return _dir() / "_originals" / storage.safe_name(file)


def preview_dir(file: str) -> Path:
    return _dir() / "_previews" / Path(storage.safe_name(file)).stem


def structure(file: str) -> dict:
    """원본 PPT를 분석해 토큰형 양식으로 바꾼다. 원본은 _originals에 보관하고 다시 구조화할 때 원본에서 시작한다."""
    from . import template_struct, thumbs

    if file == DEFAULT_TEMPLATE_NAME:
        raise ValueError("시스템 기본 템플릿은 구조화하지 않습니다.")
    cur, orig = path(file), original_path(file)
    if not cur.exists():
        raise KeyError(file)
    orig.parent.mkdir(parents=True, exist_ok=True)
    if not orig.exists():
        shutil.copy2(cur, orig)
    work = storage.DATA_DIR / "_work" / Path(storage.safe_name(file)).stem
    tmp_out = work / "structured.pptx"
    result = template_struct.structure(orig, tmp_out, work)
    shutil.copy2(tmp_out, cur)
    shutil.rmtree(preview_dir(file), ignore_errors=True)
    previews = thumbs.render(cur, preview_dir(file), width=480)
    with _lock:
        meta = _load_meta()
        meta.setdefault(file, {})["structured"] = {
            "at": storage.now(), "summary": result["summary"], "warnings": result["warnings"],
            "source_slides": result["source_slides"], "previews": len(previews)}
        _save_meta(meta)
    return get(file)
