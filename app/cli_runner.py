"""Claude Code(구독 로그인) 실행기 — 표준 라이브러리만 사용한다.

두 가지 용도:
1) 서버 내부: '이 PC의 Pro 계정' 방식에서 서버 PC의 Claude Code를 호출 (claude_client가 import)
2) 팀원 PC의 분석 실행기: 서버에서 내 분석 작업을 받아 내 PC의 Claude Code로 처리하고 결과만 돌려준다.
   웹 '⚙ 내 AI 연결'에서 이 파일을 내려받아 실행한다:
       python proposal-worker.py --server https://서버주소 --token 발급받은토큰
   한 번 실행하면 설정이 저장되어 다음부터는 python proposal-worker.py 만 실행하면 된다.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

CLI_MODEL = os.getenv("CLAUDE_CLI_MODEL", "")  # 비우면 계정 기본 모델
CLI_TIMEOUT = int(os.getenv("CLAUDE_CLI_TIMEOUT", "1500"))
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class CliError(Exception):
    pass


def cli_path() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for name in ("claude.exe", "claude"):
        default = Path.home() / ".local" / "bin" / name
        if default.exists():
            return str(default)
    return None


def cli_env() -> dict:
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
                           timeout=30, env=cli_env(), creationflags=NO_WINDOW)
        info = json.loads(r.stdout or "{}")
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as e:
        return {"installed": True, "loggedIn": False, "error": str(e), "path": exe}
    return {"installed": True, "path": exe, **info}


def _prompt(content: list[dict]) -> tuple[str, list[str]]:
    """문서 블록을 텍스트 프롬프트로 바꾼다. 스캔 PDF·이미지는 파일 경로를 주고 Read 도구로 읽게 한다."""
    parts, files = [], []
    for b in content:
        if b["type"] == "text":
            parts.append(b["text"])
        elif b["type"] == "image":
            files.append(b["_path"])
            parts.append(f'<image title="{b.get("_title", "")}">Read 도구로 이 이미지 파일을 직접 보세요: {b["_path"]}</image>')
        elif b["source"]["type"] == "text":
            parts.append(f'<document title="{b["title"]}">\n{b["source"]["data"]}\n</document>')
        else:
            files.append(b["_path"])
            parts.append(f'<document title="{b["title"]}">\n(텍스트 추출이 안 되는 PDF입니다. Read 도구로 다음 파일을 직접 읽으세요: {b["_path"]})\n</document>')
    return "\n\n".join(parts), files


def run(system: str, content: list[dict], schema: dict, light: bool = False, cwd: str | None = None) -> dict:
    """Claude Code를 비대화식으로 실행해 JSON 스키마 형식의 결과를 받는다."""
    exe = cli_path()
    if not exe:
        raise CliError("Claude Code CLI가 설치되어 있지 않습니다. 설치 후 claude auth login으로 로그인하세요.")
    prompt, files = _prompt(content)
    cmd = [exe, "-p", "--output-format", "json", "--no-session-persistence",
           "--json-schema", json.dumps(schema, ensure_ascii=False),
           "--system-prompt", system + "\n최종 답은 지정된 JSON 스키마 형식으로만 내세요."]
    if files:
        cmd += ["--tools", "Read", "--allowedTools", "Read"]
        for d in sorted({str(Path(x).parent) for x in files}):
            cmd += ["--add-dir", d]
    else:
        cmd += ["--tools", ""]
    model = "haiku" if light else CLI_MODEL
    if model:
        cmd += ["--model", model]
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           timeout=CLI_TIMEOUT, env=cli_env(), cwd=cwd or tempfile.gettempdir(),
                           creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired as e:
        raise CliError(f"Claude Code 응답이 {CLI_TIMEOUT // 60}분 안에 끝나지 않았습니다.") from e
    except OSError as e:
        raise CliError(f"Claude Code를 실행하지 못했습니다: {e}") from e

    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        msg = (r.stderr or r.stdout or "").strip()[:300]
        raise CliError(f"Claude Code 실행 오류: {msg or '응답 없음'}") from None
    if isinstance(out, list):  # 일부 버전은 이벤트 배열을 반환
        out = next((e for e in reversed(out) if e.get("type") == "result"), {})
    result_text = str(out.get("result") or "")
    if out.get("is_error") or out.get("subtype", "success") != "success":
        low = result_text.lower()
        if "login" in low or "auth" in low or "not logged" in low:
            raise CliError("Claude Code에 로그인되어 있지 않습니다. 터미널에서 claude auth login으로 로그인하세요.")
        if "limit" in low:
            raise CliError(f"Claude 구독 사용량 한도에 도달했습니다. 한도가 초기화된 뒤 다시 시도하세요. ({result_text[:120]})")
        raise CliError(f"Claude Code 오류: {result_text[:300] or out.get('subtype')}")
    data = out.get("structured_output")
    if isinstance(data, dict):
        return data
    s, e = result_text.find("{"), result_text.rfind("}")
    try:
        return json.loads(result_text[s:e + 1])
    except (json.JSONDecodeError, ValueError) as err:
        raise CliError("Claude Code 응답(JSON)을 해석하지 못했습니다.") from err


# ---------------- 팀원 PC 분석 실행기 ----------------
CONFIG = Path.home() / ".proposal-worker.json"


def run_job(job: dict) -> dict:
    """서버에서 받은 작업 실행. 원본 파일(스캔 PDF·이미지)은 임시 폴더에 풀어 Claude Code가 읽게 한다."""
    with tempfile.TemporaryDirectory(prefix="proposal-worker-") as tmp:
        content = []
        for i, b in enumerate(job["content"]):
            b = dict(b)
            if b["type"] == "image" or (b["type"] == "document" and b["source"]["type"] == "base64"):
                ext = ".png" if b["type"] == "image" else ".pdf"
                p = Path(tmp) / f"file{i}{ext}"
                p.write_bytes(base64.b64decode(b["source"]["data"]))
                b["_path"] = str(p)
            content.append(b)
        return run(job["system"], content, job["schema"], job.get("light", False), cwd=tmp)


def _http(method: str, url: str, token: str, body: dict | None = None, timeout: float = 60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json",
        "User-Agent": "proposal-worker/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return r.status, (json.loads(raw) if raw else None)


def _log_to_file() -> None:
    """pythonw(콘솔 창 없음)로 실행되면 stdout이 없으므로 스크립트 옆 worker.log에 기록한다 (1MB 넘으면 새로 시작)."""
    log = Path(__file__).resolve().with_name("worker.log")
    try:
        if log.exists() and log.stat().st_size > 1_000_000:
            log.replace(log.with_suffix(".log.old"))
        f = open(log, "a", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        f = open(os.devnull, "w")
    sys.stdout = sys.stderr = f
    print(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 실행기 시작 =====")


_LOCK = None


def _single_instance() -> bool:
    """같은 PC에서 실행기가 두 개 돌지 않게 로컬 포트를 잠금으로 쓴다."""
    global _LOCK
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 47831))
    except OSError:
        s.close()
        return False
    _LOCK = s
    return True


def worker_main(argv: list[str] | None = None) -> None:
    if sys.stdout is None or sys.stderr is None:
        _log_to_file()
    elif hasattr(sys.stdout, "reconfigure"):  # 한글이 깨지지 않게, 로그 파일로 돌려도 바로 보이게
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    if not _single_instance():
        print("[종료] 이 PC에서 실행기가 이미 실행 중입니다.")
        return
    ap = argparse.ArgumentParser(description="제안서 루틴 도우미 — 내 PC의 Claude Code로 분석하는 실행기")
    ap.add_argument("--server", help="서버 주소 (예: https://xxxx.trycloudflare.com)")
    ap.add_argument("--token", help="웹 '⚙ 내 AI 연결'에서 발급한 실행기 토큰")
    args = ap.parse_args(argv)
    saved = {}
    if CONFIG.exists():
        try:
            saved = json.loads(CONFIG.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            saved = {}
    server = (args.server or saved.get("server") or "").rstrip("/")
    token = args.token or saved.get("token") or ""
    if not server or not token:
        ap.error("처음 실행할 때는 --server 와 --token 을 함께 지정하세요.")
    if args.server or args.token:
        CONFIG.write_text(json.dumps({"server": server, "token": token}), encoding="utf-8")

    st = cli_status()
    for _ in range(12):  # PC 부팅 직후에는 잠깐 확인이 실패할 수 있어 1분까지 다시 시도한다
        if st.get("installed") and st.get("loggedIn"):
            break
        time.sleep(5)
        st = cli_status()
    if not st.get("installed"):
        print("[실패] Claude Code가 없습니다. 설치: https://claude.com/claude-code (Windows: irm https://claude.ai/install.ps1 | iex)")
        sys.exit(1)
    if not st.get("loggedIn"):
        print("[실패] Claude Code에 로그인되어 있지 않습니다. 터미널에서 claude auth login 을 먼저 실행하세요.")
        sys.exit(1)
    print(f"[시작] 서버 {server} 에 연결합니다 · Claude Code 계정: {st.get('email') or '-'}")
    print("       실행기가 켜져 있으면 웹에서 실행한 내 분석이 이 PC의 Claude Code로 처리됩니다.")
    online = False
    while True:
        try:
            if not online:  # 연결을 바로 확인한다 (poll은 작업이 없으면 25초 동안 응답하지 않음)
                _http("GET", f"{server}/api/worker/hello", token, timeout=15)
                print("[연결됨] 작업을 기다리는 중...")
                online = True
            status, job = _http("GET", f"{server}/api/worker/poll", token, timeout=60)
            if status != 200 or not job:
                continue
            print(f"[작업] {time.strftime('%H:%M:%S')} {job.get('label') or '분석'} 처리 중...")
            t = time.time()
            try:
                result = {"ok": True, "data": run_job(job)}
                print(f"[완료] {round(time.time() - t)}초")
            except CliError as e:
                result = {"ok": False, "error": str(e)}
                print(f"[오류] {e}")
            _http("POST", f"{server}/api/worker/result/{job['id']}", token, result, timeout=120)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                print("[실패] 토큰이 올바르지 않습니다. 웹 '⚙ 내 AI 연결'에서 설치 파일을 다시 받아 실행하세요.")
                sys.exit(1)
            print(f"[대기] 서버 응답 오류({e.code}) — 10초 뒤 다시 시도")
            online = False
            time.sleep(10)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if online:
                print(f"[대기] 서버에 연결할 수 없습니다({e}) — 다시 시도합니다")
            online = False
            time.sleep(5)
        except KeyboardInterrupt:
            print("\n[종료] 실행기를 껐습니다.")
            return


if __name__ == "__main__":
    worker_main()
