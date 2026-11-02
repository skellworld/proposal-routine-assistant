"""제안서 루틴 도우미 관리 스크립트 — Claude Code 같은 에이전트나 터미널에서 비대화식으로 실행한다.

사용법 (어떤 Python 3.9+로 실행해도 된다. 앱 자체는 가상환경에서 돈다):
    python manage.py setup      가상환경 생성 + 패키지 설치 + .env 준비
    python manage.py doctor     설치·Claude 연결 상태 점검
    python manage.py start      서버를 백그라운드로 시작 (이미 켜져 있으면 그대로)
    python manage.py serve      서버를 포그라운드로 실행 (Ctrl+C로 종료, 미리보기·디버깅용)
    python manage.py stop       서버 종료
    python manage.py restart    서버 재시작 (코드 변경 반영)
    python manage.py status     실행 여부와 주소 확인
    python manage.py logs [N]   서버 로그 마지막 N줄 (기본 40)
    python manage.py update     git pull → 패키지 갱신 → (실행 중이면) 재시작

옵션: 명령 뒤에 --port 8000 (기본 8000, 환경변수 PORT로도 지정)
종료 코드: 0 성공, 1 실패 — 출력은 사람이 읽기 쉬운 한국어 한 줄 요약 위주.
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# 가상환경은 Google 드라이브 등 동기화 폴더를 피하기 위해 사용자 폴더에 둔다 (setup.bat과 같은 위치)
VENV = Path.home() / ".proposal-helper" / "venv"
WIN = os.name == "nt"
VPY = VENV / ("Scripts/python.exe" if WIN else "bin/python")
WORK = ROOT / "data" / "_work"
PID_FILE = WORK / "server.pid"
LOG_FILE = WORK / "server.log"

if hasattr(sys.stdout, "reconfigure"):  # Windows 콘솔(cp949)에서도 한글·기호가 깨지지 않게
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def say(msg: str) -> None:
    print(msg, flush=True)


def fail(msg: str) -> None:
    say(f"[실패] {msg}")
    sys.exit(1)


def url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(url(port) + "/api/settings", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def port_pids(port: int) -> list[int]:
    """해당 포트에서 LISTEN 중인 프로세스 ID."""
    pids: set[int] = set()
    try:
        if WIN:
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                                 errors="replace").stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[3].upper() == "LISTENING":
                    pids.add(int(parts[4]))
        elif shutil.which("lsof"):
            out = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True,
                                 text=True).stdout
            pids.update(int(x) for x in out.split() if x.isdigit())
    except (OSError, ValueError):
        pass
    return sorted(pids)


def kill(pid: int) -> None:
    if WIN:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


# ---------------- 명령 ----------------
def cmd_setup(args) -> None:
    if sys.version_info < (3, 11):
        say(f"[주의] 지금 Python은 {sys.version.split()[0]}입니다. 3.11 이상을 권장합니다.")
    if not VPY.exists():
        say(f"[1/3] 가상환경 생성: {VENV}")
        VENV.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    else:
        say(f"[1/3] 가상환경 있음: {VENV}")
    say("[2/3] 패키지 설치...")
    subprocess.run([str(VPY), "-m", "pip", "install", "-q", "--upgrade", "pip"], check=True)
    r = subprocess.run([str(VPY), "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")])
    if r.returncode:
        fail("패키지 설치에 실패했습니다. 위 오류를 확인하세요.")
    say("[3/3] 설정 파일 준비...")
    env = ROOT / ".env"
    if not env.exists():
        shutil.copy(ROOT / ".env.example", env)
        say("  .env 생성 (API 키 방식을 쓰려면 ANTHROPIC_API_KEY를 입력)")
    say("[완료] 설치가 끝났습니다. 다음: python manage.py doctor → python manage.py start")


def cmd_doctor(args) -> None:
    ok = True
    say(f"- Python(관리 스크립트): {sys.version.split()[0]}")
    if not VPY.exists():
        fail(f"가상환경이 없습니다({VENV}). python manage.py setup 을 먼저 실행하세요.")
    probe = r"""
import json, sys
from dotenv import load_dotenv
load_dotenv()
import fastapi, pptx, pypdf, docx, olefile, matplotlib, anthropic
from app import claude_client as c
print(json.dumps({"python": sys.version.split()[0], "api_key": c.has_api_key(),
                  "backend": c.active_backend(), "cli": c.cli_status()}, ensure_ascii=False))
"""
    r = subprocess.run([str(VPY), "-c", probe], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode:
        fail("앱 패키지를 불러오지 못했습니다. python manage.py setup 을 다시 실행하세요.\n" + r.stderr[-800:])
    info = json.loads(r.stdout.strip().splitlines()[-1])
    say(f"- 가상환경 Python: {info['python']} · 패키지 정상")
    cli = info["cli"]
    if cli.get("installed"):
        logged = cli.get("loggedIn")
        say(f"- Claude Code CLI: 설치됨 · {'로그인됨 (' + str(cli.get('email') or cli.get('subscriptionType') or '') + ')' if logged else '로그인 안 됨'}")
    else:
        say("- Claude Code CLI: 설치 안 됨")
    say(f"- API 키(.env): {'있음' if info['api_key'] else '없음'}")
    say(f"- 사용할 방식: {'API 키' if info['backend'] == 'api' else 'Pro 계정(Claude Code CLI)'}")
    if info["backend"] == "api" and not info["api_key"]:
        ok = False
        say("[문제] API 방식인데 .env에 ANTHROPIC_API_KEY가 없습니다.")
    if info["backend"] == "cli" and not (cli.get("installed") and cli.get("loggedIn")):
        ok = False
        say("[문제] Claude 연결이 준비되지 않았습니다. 둘 중 하나를 하세요:\n"
            "  (1) Claude Code 설치 후 `claude auth login`으로 Pro/Max 계정 로그인\n"
            "  (2) .env에 ANTHROPIC_API_KEY 입력")
    port = args.port
    say(f"- 서버: {'실행 중 ' + url(port) if alive(port) else '꺼져 있음'}")
    say("[완료] 점검 통과" if ok else "[주의] 위 문제를 해결해야 분석 기능이 동작합니다.")
    if not ok:
        sys.exit(1)


def cmd_start(args) -> None:
    port = args.port
    if alive(port):
        say(f"[완료] 이미 실행 중입니다: {url(port)}")
        return
    if port_pids(port):
        fail(f"{port} 포트를 다른 프로그램이 쓰고 있습니다. --port 로 다른 포트를 지정하세요.")
    if not VPY.exists():
        fail(f"가상환경이 없습니다({VENV}). python manage.py setup 을 먼저 실행하세요.")
    WORK.mkdir(parents=True, exist_ok=True)
    log = open(LOG_FILE, "ab")
    log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start (port {port}) =====\n".encode())
    log.flush()
    kw = {"cwd": ROOT, "stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if WIN:
        # DETACHED_PROCESS면 자식 콘솔 프로그램(claude.exe 등)이 새 창을 띄우므로 숨은 콘솔을 준다
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.Popen([str(VPY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                          "--port", str(port)], env=env, **kw)
    PID_FILE.write_text(f"{p.pid} {port}", encoding="utf-8")
    for _ in range(60):
        if alive(port):
            pids = port_pids(port) or [p.pid]  # Windows venv의 python.exe는 실제 서버를 자식으로 띄운다
            say(f"[완료] 서버 시작: {url(port)} (PID {pids[0]}, 로그 {LOG_FILE.relative_to(ROOT)})")
            return
        if p.poll() is not None:
            break
        time.sleep(0.5)
    tail = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
    fail("서버가 시작되지 않았습니다. 로그:\n" + "\n".join(tail))


def cmd_serve(args) -> None:
    """포그라운드 실행 — Claude 데스크톱 앱 미리보기(.claude/launch.json)나 디버깅용. Ctrl+C로 종료."""
    if not VPY.exists():
        fail(f"가상환경이 없습니다({VENV}). python manage.py setup 을 먼저 실행하세요.")
    if alive(args.port):
        fail(f"이미 {url(args.port)} 에서 실행 중입니다. python manage.py stop 후 다시 시도하세요.")
    try:
        sys.exit(subprocess.call([str(VPY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                  "--port", str(args.port)], cwd=ROOT))
    except KeyboardInterrupt:
        pass


def cmd_stop(args) -> None:
    port = args.port
    pids = set(port_pids(port))
    if PID_FILE.exists():
        try:
            pid, pport = PID_FILE.read_text().split()
            if int(pport) == port:
                pids.add(int(pid))
        except ValueError:
            pass
    if not pids and not alive(port):
        say("[완료] 실행 중인 서버가 없습니다.")
        PID_FILE.unlink(missing_ok=True)
        return
    killed: set[int] = set()
    for _ in range(30):  # 막 시작하는 중인 서버는 포트를 늦게 잡으므로 다시 찾아 종료한다
        for pid in pids - killed:
            kill(pid)
            killed.add(pid)
        time.sleep(0.3)
        pids = set(port_pids(port))
        if not pids and not alive(port):
            break
    pids = killed
    PID_FILE.unlink(missing_ok=True)
    if alive(port):
        fail(f"서버를 끄지 못했습니다(PID {sorted(pids)}). 서버 창을 직접 닫아 주세요.")
    say("[완료] 서버를 종료했습니다.")


def cmd_restart(args) -> None:
    cmd_stop(args)
    cmd_start(args)


def cmd_status(args) -> None:
    port = args.port
    if alive(port):
        with urllib.request.urlopen(url(port) + "/api/settings", timeout=3) as r:
            info = json.loads(r.read())
        mode = "API 키" if info.get("active") == "api" else "Pro 계정(CLI)"
        say(f"[실행 중] {url(port)} · Claude 연결: {mode} · PID {port_pids(port) or '?'}")
    else:
        say(f"[꺼짐] {url(port)} 에서 응답이 없습니다. 시작: python manage.py start")


def cmd_logs(args) -> None:
    if not LOG_FILE.exists():
        say("로그가 아직 없습니다. (manage.py start로 시작한 서버만 로그를 남깁니다)")
        return
    lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    say("\n".join(lines[-args.n:]))


def cmd_update(args) -> None:
    if not shutil.which("git"):
        fail("git이 없습니다. Git을 설치하세요.")
    r = subprocess.run(["git", "pull", "--ff-only"], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    say((r.stdout or r.stderr).strip())
    if r.returncode:
        fail("git pull 실패 — 이 PC에서 고친 파일이 있으면 먼저 커밋하거나 되돌리세요.")
    if VPY.exists():
        subprocess.run([str(VPY), "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")], check=True)
        say("- 패키지 갱신 완료")
    else:
        cmd_setup(args)
    if alive(args.port):
        cmd_restart(args)
    say("[완료] 업데이트가 끝났습니다.")


def main() -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    ap = argparse.ArgumentParser(description="제안서 루틴 도우미 관리")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("setup", "doctor", "start", "serve", "stop", "restart", "status", "update"):
        sub.add_parser(name, parents=[common])
    lp = sub.add_parser("logs", parents=[common])
    lp.add_argument("n", nargs="?", type=int, default=40)
    args = ap.parse_args()
    os.chdir(ROOT)
    globals()[f"cmd_{args.cmd}"](args)


if __name__ == "__main__":
    main()
