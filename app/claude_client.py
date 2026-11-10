"""AI 호출 (구조화 JSON 출력) — 로그인한 사용자의 개인 AI 연결 설정으로 호출한다.

제공자(provider):
- anthropic: 사용자 본인의 Anthropic API 키 (Claude)
- openai: 사용자 본인의 OpenAI API 키 (ChatGPT)
- cli: 서버 PC에 로그인된 Claude Code(Pro/Max 구독) — 서버 PC에서 직접 접속한 경우에만 허용
- worker: 사용자 본인 PC에서 도는 분석 실행기(cli_runner.py)가 본인 Claude Code로 처리 — 결과는 서버에 저장되어 공유
요청마다 use()로 설정을 지정하며, 백그라운드 작업은 bound()로 감싸 설정을 이어받는다.
"""
import base64
import contextvars
import json
import os
import threading
import time
import uuid
from pathlib import Path

import anthropic
import openai

from . import cli_runner
from .extractors import ExtractError, extract_text

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
OPENAI_TIMEOUT = float(os.getenv("OPENAI_TIMEOUT", "1500"))
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


_ctx: contextvars.ContextVar = contextvars.ContextVar("ai_config", default=None)


def use(cfg: dict | None):
    """이 요청(스레드 문맥)에서 쓸 AI 설정을 지정한다."""
    return _ctx.set(cfg)


def bound(fn):
    """현재 AI 설정을 이어받아 다른 스레드에서 실행되도록 감싼다."""
    ctx = contextvars.copy_context()
    return lambda *a, **k: ctx.run(fn, *a, **k)


def _cfg() -> dict:
    cfg = _ctx.get()
    if not cfg or cfg.get("provider") in (None, "", "none"):
        raise ClaudeError("AI가 연결되어 있지 않아 AI 분석 기능을 쓸 수 없습니다. 왼쪽 아래 '⚙ 내 AI 연결'에서 AI를 연결하세요.")
    return cfg


# 서버 PC의 Claude Code 호출은 cli_runner(표준 라이브러리 전용, 팀원 PC 실행기와 공용)가 맡는다
cli_path = cli_runner.cli_path
cli_status = cli_runner.cli_status
CLI_TIMEOUT = cli_runner.CLI_TIMEOUT


def active_backend() -> str:
    return (_ctx.get() or {}).get("provider", "")


def available() -> bool:
    """현재 사용자 설정으로 AI 호출이 가능해 보이는지 (빠른 확인)."""
    cfg = _ctx.get() or {}
    if cfg.get("provider") == "cli":
        return bool(cfg.get("local")) and cli_path() is not None
    if cfg.get("provider") == "worker":
        return worker_online(cfg.get("username", ""))
    return bool(cfg.get("provider") and cfg.get("key"))


def call_json(system: str, content: list[dict], schema: dict, max_tokens: int = 64000,
              effort: str | None = None, light: bool = False) -> dict:
    """light=True: 짧은 보조 작업(템플릿 설명 등) — 가벼운 설정으로 호출."""
    cfg = _cfg()
    if cfg["provider"] == "cli":
        if not cfg.get("local"):
            raise ClaudeError("'이 PC의 Pro 계정'은 서버 PC에서 직접 접속할 때만 쓸 수 있습니다. 내 AI 연결에서 다른 방식을 설정하세요.")
        try:
            return cli_runner.run(system, content, schema, light,
                                  cwd=str(Path(__file__).resolve().parent.parent / "data"))
        except cli_runner.CliError as e:
            raise ClaudeError(str(e)) from e
    if cfg["provider"] == "worker":
        return _call_worker(cfg, system, content, schema, light)
    if cfg["provider"] == "openai":
        return _call_openai(cfg, system, content, schema, max_tokens)
    return _call_api(cfg, system, content, schema, max_tokens, effort or ("low" if light else EFFORT))


# ---------------- 팀원 PC 분석 실행기(worker) 대기열 ----------------
# 실행기는 서버로 나가는 연결만 쓴다: /api/worker/poll 로 내 작업을 받아 가고 /api/worker/result 로 결과를 보낸다.
WORKER_ONLINE_SEC = 75
_wq = threading.Condition()
_queues: dict = {}  # username -> [job, ...] (아직 가져가지 않은 작업)
_jobs: dict = {}    # job id -> job
_seen: dict = {}    # username -> 마지막 poll 시각


def worker_online(username: str) -> bool:
    return time.time() - _seen.get(username, 0) < WORKER_ONLINE_SEC


def worker_take(username: str, wait: float = 25.0) -> dict | None:
    """실행기의 long-poll: 내 작업이 생길 때까지 최대 wait초 기다린다."""
    end = time.time() + wait
    with _wq:
        while True:
            _seen[username] = time.time()
            q = _queues.get(username) or []
            if q:
                job = q.pop(0)
                job["taken"] = True
                return job["payload"]
            left = end - time.time()
            if left <= 0:
                return None
            _wq.wait(min(left, 5))


def worker_finish(username: str, job_id: str, result: dict) -> bool:
    job = _jobs.get(job_id)
    if not job or job["user"] != username:
        return False
    job["result"] = result
    job["event"].set()
    return True


def _call_worker(cfg: dict, system: str, content: list[dict], schema: dict, light: bool) -> dict:
    user = cfg.get("username", "")
    if not worker_online(user):
        raise ClaudeError("내 PC의 분석 실행기가 꺼져 있습니다. 내 PC에서 실행기(proposal-worker.py)를 켠 뒤 다시 시도하세요.")
    # 서버 파일 경로(_path)는 보내지 않는다 — 실행기가 원본을 자기 PC 임시 폴더에 풀어 쓴다
    blocks = [{k: v for k, v in b.items() if k == "_title" or not k.startswith("_")} for b in content]
    jid = uuid.uuid4().hex
    job = {"user": user, "event": threading.Event(), "result": None, "taken": False,
           "payload": {"id": jid, "system": system, "content": blocks, "schema": schema, "light": light}}
    with _wq:
        _jobs[jid] = job
        _queues.setdefault(user, []).append(job)
        _wq.notify_all()
    try:
        deadline = time.time() + CLI_TIMEOUT + 120
        while not job["event"].wait(5):
            if not job["taken"] and not worker_online(user):
                raise ClaudeError("내 PC의 분석 실행기와 연결이 끊겼습니다. 실행기를 다시 켠 뒤 다시 시도하세요.")
            if time.time() > deadline:
                raise ClaudeError("내 PC의 분석 실행기가 제시간에 응답하지 않았습니다.")
    finally:
        with _wq:
            _jobs.pop(jid, None)
            q = _queues.get(user) or []
            if job in q:
                q.remove(job)
    r = job["result"] or {}
    if not r.get("ok"):
        raise ClaudeError(r.get("error") or "내 PC의 분석 실행기에서 오류가 발생했습니다.")
    return r.get("data") or {}


def list_models(provider: str, key: str) -> list[str]:
    """키 확인 겸 사용할 수 있는 모델 목록 (최신순)."""
    try:
        if provider == "anthropic":
            return [m.id for m in anthropic.Anthropic(api_key=key, timeout=30).models.list(limit=100)]
        if provider == "openai":
            ms = openai.OpenAI(api_key=key, timeout=30).models.list()
            ids = [m.id for m in sorted(ms, key=lambda m: m.created, reverse=True)]
            # 대화형 분석에 쓸 수 있는 계열만 (임베딩·음성·이미지 모델 제외)
            skip = ("embed", "audio", "tts", "whisper", "dall-e", "image", "moderation", "realtime",
                    "transcribe", "search", "davinci", "babbage", "sora", "codex")
            return [i for i in ids if i.startswith(("gpt-", "o")) and not any(s in i for s in skip)]
    except (anthropic.AuthenticationError, openai.AuthenticationError) as e:
        raise ClaudeError("API 키가 올바르지 않습니다. 키를 다시 확인하세요.") from e
    except (anthropic.APIConnectionError, openai.APIConnectionError) as e:
        raise ClaudeError("AI 서버에 연결할 수 없습니다. 네트워크를 확인하세요.") from e
    except (anthropic.APIStatusError, openai.APIStatusError) as e:
        raise ClaudeError(f"모델 목록을 가져오지 못했습니다({e.status_code}).") from e
    raise ClaudeError("지원하지 않는 AI입니다.")


# ---------------- Anthropic API 방식 ----------------
_clients: dict = {}


def _api_client(key: str) -> anthropic.Anthropic:
    if key not in _clients:
        _clients[key] = anthropic.Anthropic(api_key=key)
    return _clients[key]


def _call_api(cfg: dict, system: str, content: list[dict], schema: dict, max_tokens: int, effort: str) -> dict:
    if not cfg.get("key"):
        raise ClaudeError("저장된 Claude API 키가 없습니다. 내 AI 연결에서 키를 다시 입력하세요.")
    content = [{k: v for k, v in b.items() if not k.startswith("_")} for b in content]
    try:
        with _api_client(cfg["key"]).beta.messages.stream(
            model=cfg.get("model") or MODEL,
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
        raise ClaudeError("Claude API 키 인증에 실패했습니다. 내 AI 연결에서 키를 확인하세요.") from e
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


# ---------------- OpenAI(ChatGPT) API 방식 ----------------
def _openai_input(content: list[dict]) -> list[dict]:
    parts = []
    for b in content:
        if b["type"] == "text":
            parts.append({"type": "input_text", "text": b["text"]})
        elif b["type"] == "image":
            parts.append({"type": "input_image", "image_url": f"data:image/png;base64,{b['source']['data']}"})
        elif b["source"]["type"] == "text":
            parts.append({"type": "input_text", "text": f'<document title="{b["title"]}">\n{b["source"]["data"]}\n</document>'})
        else:  # 텍스트 추출이 안 되는 PDF는 파일 그대로 보낸다
            parts.append({"type": "input_file", "filename": Path(b.get("_path") or "document.pdf").name,
                          "file_data": f"data:application/pdf;base64,{b['source']['data']}"})
    return [{"role": "user", "content": parts}]


def _call_openai(cfg: dict, system: str, content: list[dict], schema: dict, max_tokens: int) -> dict:
    if not cfg.get("key"):
        raise ClaudeError("저장된 OpenAI API 키가 없습니다. 내 AI 연결에서 키를 다시 입력하세요.")
    client = openai.OpenAI(api_key=cfg["key"], timeout=OPENAI_TIMEOUT)
    try:
        r = client.responses.create(
            model=cfg["model"],
            instructions=system,
            input=_openai_input(content),  # 출력 길이 상한은 모델 기본값(모델마다 최대치가 달라 지정하지 않음)
            text={"format": {"type": "json_schema", "name": "result", "schema": schema, "strict": True}},
            store=False,
        )
    except openai.AuthenticationError as e:
        raise ClaudeError("OpenAI API 키 인증에 실패했습니다. 내 AI 연결에서 키를 확인하세요.") from e
    except openai.RateLimitError as e:
        raise ClaudeError("OpenAI 사용량 한도(또는 크레딧)에 도달했습니다. 잠시 후 다시 시도하거나 결제 상태를 확인하세요.") from e
    except openai.BadRequestError as e:
        raise ClaudeError(f"요청 오류: {e.message}") from e
    except openai.APIStatusError as e:
        raise ClaudeError(f"OpenAI API 오류({e.status_code}): {e.message}") from e
    except openai.APIConnectionError as e:
        raise ClaudeError("OpenAI API에 연결할 수 없습니다. 네트워크를 확인하세요.") from e
    if r.status == "incomplete":
        reason = getattr(r.incomplete_details, "reason", "")
        if reason == "max_output_tokens":
            raise ClaudeError("응답이 너무 길어 잘렸습니다. 문서를 나누어 다시 시도하세요.")
        raise ClaudeError(f"ChatGPT 응답이 완료되지 않았습니다({reason}).")
    for item in r.output:  # 거절 응답
        for c in getattr(item, "content", None) or []:
            if getattr(c, "type", "") == "refusal":
                raise ClaudeError("ChatGPT가 이 요청을 처리하지 않았습니다(refusal).")
    try:
        return json.loads(r.output_text)
    except json.JSONDecodeError as e:
        raise ClaudeError("ChatGPT 응답(JSON)을 해석하지 못했습니다.") from e
