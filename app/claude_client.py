"""Claude 호출 (구조화 JSON 출력).

두 가지 방식을 지원한다.
- api: Anthropic API 키(.env의 ANTHROPIC_API_KEY)로 호출 — 사용량만큼 과금
- cli: PC에 설치된 Claude Code CLI(claude.exe)를 Pro/Max 계정 로그인 상태로 호출 — 구독 사용량 한도 내
설정(data/settings.json)의 backend 값: auto(키가 있으면 api, 없으면 cli) | api | cli
"""
import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

import anthropic

from . import settings
from .extractors import ExtractError, extract_text

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("CLAUDE_EFFORT", "high")
CLI_MODEL = os.getenv("CLAUDE_CLI_MODEL", "")  # 비우면 계정 기본 모델
CLI_TIMEOUT = int(os.getenv("CLAUDE_CLI_TIMEOUT", "1500"))


class ClaudeError(Exception):
    pass


# ---------------- 문서 블록 ----------------
def document_blocks(paths: list[Path], label: str) -> list[dict]:
    """파일들을 Claude 문서 블록으로 변환. 텍스트가 없는 PDF(스캔본)는 원본을 그대로 보낸다.
    '_path' 키는 CLI 방식에서 원본 파일을 읽히기 위한 내부용이며 API 전송 전에 제거한다."""
    blocks = []
    for p in paths:
        title = f"[{label}] {p.name}"
        try:
            text = extract_text(p)
        except ExtractError:
            if p.suffix.lower() != ".pdf":
                raise
            text = ""
        if text:
            blocks.append({
                "type": "document",
                "title": title,
                "source": {"type": "text", "media_type": "text/plain", "data": text},
            })
        elif p.suffix.lower() == ".pdf":
            blocks.append({
                "type": "document",
                "title": title,
                "_path": str(p),
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.b64encode(p.read_bytes()).decode(),
                },
            })
        else:
            raise ExtractError(f"{p.name}에서 텍스트를 추출하지 못했습니다.")
    return blocks


# ---------------- 방식 선택 ----------------
def image_block(path: Path, title: str = "") -> dict:
    return {"type": "image", "_path": str(path), "_title": title,
            "source": {"type": "base64", "media_type": "image/png",
                       "data": base64.b64encode(Path(path).read_bytes()).decode()}}


def has_api_key() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def cli_path() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for name in ("claude.exe", "claude"):
        default = Path.home() / ".local" / "bin" / name
        if default.exists():
            return str(default)
    return None


def _cli_env() -> dict:
    env = dict(os.environ)
    # API 키가 환경에 있으면 CLI가 구독 대신 키로 과금하므로 제거한다
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(k, None)
    return env


def cli_status() -> dict:
    exe = cli_path()
    if not exe:
        return {"installed": False, "loggedIn": False}
    try:
        r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, encoding="utf-8",
                           timeout=30, env=_cli_env(),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        info = json.loads(r.stdout or "{}")
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as e:
        return {"installed": True, "loggedIn": False, "error": str(e), "path": exe}
    return {"installed": True, "path": exe, **info}


def active_backend() -> str:
    choice = settings.get("backend", "auto")
    if choice in ("api", "cli"):
        return choice
    return "api" if has_api_key() else "cli"


def available() -> bool:
    """현재 설정으로 Claude 호출이 가능해 보이는지 (빠른 확인)."""
    return has_api_key() if active_backend() == "api" else cli_path() is not None


def call_json(system: str, content: list[dict], schema: dict, max_tokens: int = 64000,
              effort: str | None = None, light: bool = False) -> dict:
    """light=True: 짧은 보조 작업(템플릿 설명 등) — 가벼운 설정으로 호출."""
    if active_backend() == "cli":
        return _call_cli(system, content, schema, light)
    return _call_api(system, content, schema, max_tokens, effort or ("low" if light else EFFORT))


# ---------------- API 방식 ----------------
_client: anthropic.Anthropic | None = None


def _api_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not has_api_key():
            raise ClaudeError(".env 파일에 ANTHROPIC_API_KEY가 없습니다. 설정에서 'Pro 계정(Claude Code)' 방식을 선택하거나 키를 입력하세요.")
        _client = anthropic.Anthropic()
    return _client


def _call_api(system: str, content: list[dict], schema: dict, max_tokens: int, effort: str) -> dict:
    content = [{k: v for k, v in b.items() if not k.startswith("_")} for b in content]
    try:
        with _api_client().beta.messages.stream(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise ClaudeError("API 키 인증에 실패했습니다. .env의 ANTHROPIC_API_KEY를 확인하세요.") from e
    except anthropic.RateLimitError as e:
        raise ClaudeError("API 사용량 한도에 도달했습니다. 잠시 후 다시 시도하세요.") from e
    except anthropic.BadRequestError as e:
        raise ClaudeError(f"요청 오류: {e.message}") from e
    except anthropic.APIStatusError as e:
        raise ClaudeError(f"Claude API 오류({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ClaudeError("Claude API에 연결할 수 없습니다. 네트워크를 확인하세요.") from e

    if msg.stop_reason == "refusal":
        raise ClaudeError("Claude가 이 요청을 처리하지 않았습니다(refusal).")
    if msg.stop_reason == "max_tokens":
        raise ClaudeError("응답이 너무 길어 잘렸습니다. 문서를 나누어 다시 시도하세요.")
    text = next((b.text for b in msg.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ClaudeError("Claude 응답(JSON)을 해석하지 못했습니다.") from e


# ---------------- CLI(Pro 계정) 방식 ----------------
def _cli_prompt(content: list[dict]) -> tuple[str, list[str]]:
    """문서 블록을 텍스트 프롬프트로 바꾼다. 스캔 PDF는 파일 경로를 주고 Read 도구로 읽게 한다."""
    parts, pdfs = [], []
    for b in content:
        if b["type"] == "text":
            parts.append(b["text"])
        elif b["type"] == "image":
            pdfs.append(b["_path"])
            parts.append(f'<image title="{b.get("_title", "")}">Read 도구로 이 이미지 파일을 직접 보세요: {b["_path"]}</image>')
        elif b["source"]["type"] == "text":
            parts.append(f'<document title="{b["title"]}">\n{b["source"]["data"]}\n</document>')
        else:
            pdfs.append(b["_path"])
            parts.append(f'<document title="{b["title"]}">\n(텍스트 추출이 안 되는 PDF입니다. Read 도구로 다음 파일을 직접 읽으세요: {b["_path"]})\n</document>')
    return "\n\n".join(parts), pdfs


def _call_cli(system: str, content: list[dict], schema: dict, light: bool) -> dict:
    exe = cli_path()
    if not exe:
        raise ClaudeError("Claude Code CLI가 설치되어 있지 않습니다. 설정 페이지의 안내에 따라 설치·로그인하세요.")
    prompt, pdfs = _cli_prompt(content)
    cmd = [exe, "-p", "--output-format", "json", "--no-session-persistence",
           "--json-schema", json.dumps(schema, ensure_ascii=False),
           "--system-prompt", system + "\n최종 답은 지정된 JSON 스키마 형식으로만 내세요."]
    if pdfs:
        cmd += ["--tools", "Read", "--allowedTools", "Read"]
        for d in sorted({str(Path(x).parent) for x in pdfs}):
            cmd += ["--add-dir", d]
    else:
        cmd += ["--tools", ""]
    model = "haiku" if light else CLI_MODEL
    if model:
        cmd += ["--model", model]
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           timeout=CLI_TIMEOUT, env=_cli_env(), cwd=str(Path(__file__).resolve().parent.parent / "data"),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as e:
        raise ClaudeError(f"Claude Code 응답이 {CLI_TIMEOUT // 60}분 안에 끝나지 않았습니다.") from e
    except OSError as e:
        raise ClaudeError(f"Claude Code를 실행하지 못했습니다: {e}") from e

    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        msg = (r.stderr or r.stdout or "").strip()[:300]
        raise ClaudeError(f"Claude Code 실행 오류: {msg or '응답 없음'}") from None
    if isinstance(out, list):  # 일부 버전은 이벤트 배열을 반환
        out = next((e for e in reversed(out) if e.get("type") == "result"), {})
    result_text = str(out.get("result") or "")
    if out.get("is_error") or out.get("subtype", "success") != "success":
        low = result_text.lower()
        if "login" in low or "auth" in low or "not logged" in low:
            raise ClaudeError("Claude Code에 로그인되어 있지 않습니다. 설정 페이지에서 로그인 상태를 확인하세요.")
        if "limit" in low:
            raise ClaudeError(f"Pro 사용량 한도에 도달했습니다. 한도가 초기화된 뒤 다시 시도하세요. ({result_text[:120]})")
        raise ClaudeError(f"Claude Code 오류: {result_text[:300] or out.get('subtype')}")
    data = out.get("structured_output")
    if isinstance(data, dict):
        return data
    s, e = result_text.find("{"), result_text.rfind("}")
    try:
        return json.loads(result_text[s:e + 1])
    except (json.JSONDecodeError, ValueError) as err:
        raise ClaudeError("Claude Code 응답(JSON)을 해석하지 못했습니다.") from err
