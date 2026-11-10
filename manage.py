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
    python manage.py tunnel start|stop|status   외부 접속용 Cloudflare 터널 (https 주소 발급)

옵션: 명령 뒤에 --port 8000 (기본 8000, 환경변수 PORT로도 지정)
      --host 0.0.0.0 (같은 망의 다른 PC에서 IP로 접속 허용. .env에 HOST=0.0.0.0 으로 고정 가능)
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


def bind_host(args) -> str:
    """서버가 연결을 받을 주소. 기본은 이 PC 전용(127.0.0.1), 0.0.0.0이면 같은 망의 다른 PC도 IP로 접속."""
    return args.host or env_value("HOST") or "127.0.0.1"


def lan_urls(port: int) -> list[str]:
    import socket
    ips = set()
    try:  # 실제로 쓰고 있는 네트워크의 IP (연결이 끊긴 어댑터는 이름 조회에 남아 있을 수 있음)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            primary = s.getsockname()[0]
        if not primary.startswith("127."):
            return [f"http://{primary}:{port}"]
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith(("127.", "169.254.")):
                ips.add(ip)
    except OSError:
        pass
    return [f"http://{ip}:{port}" for ip in sorted(ips)]


def alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(url(port) + "/api/health", timeout=3) as r:
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
        say("  .env 생성 (AI 연결은 웹 화면에서 사용자마다 설정)")
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
import fastapi, pptx, pypdf, docx, olefile, matplotlib, anthropic, openai, cryptography
from app import claude_client as c, auth
print(json.dumps({"python": sys.version.split()[0], "users": len(auth.list_users()),
                  "cli": c.cli_status()}, ensure_ascii=False))
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
        say("- Claude Code CLI: 설치 안 됨 (선택 — 서버 PC에서 직접 접속해 Pro 계정으로 분석할 때만 필요)")
    say(f"- 계정: {str(info['users']) + '명' if info['users'] else '없음 → 서버 PC 브라우저에서 접속해 관리자 계정을 만드세요'}")
    say("- AI 연결: 사용자마다 '⚙ 내 AI 연결'에서 본인 Claude 또는 ChatGPT API 키를 설정합니다")
    exe = cloudflared_path()
    say(f"- cloudflared(외부 접속용): {'설치됨' if exe else '설치 안 됨 (winget install Cloudflare.cloudflared)'}")
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
    host = bind_host(args)
    p = subprocess.Popen([str(VPY), "-m", "uvicorn", "app.main:app", "--host", host,
                          "--port", str(port)], env=env, **kw)
    PID_FILE.write_text(f"{p.pid} {port}", encoding="utf-8")
    for _ in range(60):
        if alive(port):
            pids = port_pids(port) or [p.pid]  # Windows venv의 python.exe는 실제 서버를 자식으로 띄운다
            say(f"[완료] 서버 시작: {url(port)} (PID {pids[0]}, 로그 {LOG_FILE.relative_to(ROOT)})")
            if host != "127.0.0.1":
                say("  같은 망의 다른 PC에서: " + (" / ".join(lan_urls(port)) or "이 PC의 IP:" + str(port)))
                say("  (접속이 안 되면 Windows 방화벽에서 TCP " + str(port) + " 인바운드를 허용해야 합니다)")
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
        sys.exit(subprocess.call([str(VPY), "-m", "uvicorn", "app.main:app", "--host", bind_host(args),
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
        with urllib.request.urlopen(url(port) + "/api/health", timeout=3) as r:
            info = json.loads(r.read())
        users = "계정 있음" if info.get("users") else "최초 설정 필요(서버 PC 브라우저에서 접속)"
        say(f"[실행 중] {url(port)} · {users} · PID {port_pids(port) or '?'}")
        pub = tunnel_url()
        if pub:
            say(f"  외부 주소: {pub}")
    else:
        say(f"[꺼짐] {url(port)} 에서 응답이 없습니다. 시작: python manage.py start")


# ---------------- 외부 접속 (Cloudflare Tunnel) ----------------
TUNNEL_PID = WORK / "tunnel.pid"
TUNNEL_LOG = WORK / "tunnel.log"


def env_value(name: str) -> str:
    """.env에서 값 읽기 (관리 스크립트는 dotenv 없이 동작해야 하므로 직접 파싱)."""
    if os.getenv(name):
        return os.environ[name]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == name:
                return v.strip().strip('"').strip("'")
    return ""


def cloudflared_path() -> str | None:
    found = shutil.which("cloudflared")
    if found:
        return found
    for p in (Path(os.getenv("ProgramFiles(x86)", "")) / "cloudflared" / "cloudflared.exe",
              Path(os.getenv("ProgramFiles", "")) / "cloudflared" / "cloudflared.exe",
              Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "cloudflared.exe"):
        if p.is_file():
            return str(p)
    return None


def tunnel_pid() -> int | None:
    if not TUNNEL_PID.exists():
        return None
    try:
        pid = int(TUNNEL_PID.read_text().split()[0])
    except (ValueError, IndexError):
        return None
    if WIN:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                             errors="replace").stdout
        return pid if "cloudflared" in out.lower() else None
    try:
        os.kill(pid, 0)
        return pid
    except OSError:
        return None


def tunnel_url() -> str:
    if not tunnel_pid():
        return ""
    fixed = env_value("PUBLIC_URL")
    if fixed:
        return fixed
    if TUNNEL_LOG.exists():
        import re
        m = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", TUNNEL_LOG.read_text(encoding="utf-8", errors="replace"))
        if m:
            return m[-1]
    return "(주소 확인 중 — 잠시 후 python manage.py tunnel status)"


def cmd_tunnel(args) -> None:
    action = args.action
    pid = tunnel_pid()
    if action == "status":
        say(f"[실행 중] 외부 주소: {tunnel_url()} (PID {pid})" if pid else "[꺼짐] 외부 접속 터널이 꺼져 있습니다. 시작: python manage.py tunnel start")
        return
    if action == "stop":
        if pid:
            kill(pid)
        TUNNEL_PID.unlink(missing_ok=True)
        say("[완료] 외부 접속 터널을 껐습니다." if pid else "[완료] 실행 중인 터널이 없습니다.")
        return
    # start
    if pid:
        say(f"[완료] 이미 실행 중입니다: {tunnel_url()}")
        return
    exe = cloudflared_path()
    if not exe:
        fail("cloudflared가 없습니다. PowerShell에서 winget install Cloudflare.cloudflared 로 설치한 뒤 새 창에서 다시 실행하세요.")
    if not alive(args.port):
        fail("앱 서버가 꺼져 있습니다. 먼저 python manage.py start")
    token = env_value("CLOUDFLARE_TUNNEL_TOKEN")
    # 토큰이 있으면 Cloudflare 계정의 고정 주소(이름 있는 터널), 없으면 임시 주소(trycloudflare.com, 켤 때마다 바뀜)
    cmd = [exe, "tunnel", "--no-autoupdate"] + (["run", "--token", token] if token else ["--url", url(args.port)])
    WORK.mkdir(parents=True, exist_ok=True)
    TUNNEL_LOG.write_text("", encoding="utf-8")
    log = open(TUNNEL_LOG, "ab")
    kw = {"cwd": ROOT, "stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if WIN:
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
    p = subprocess.Popen(cmd, **kw)
    TUNNEL_PID.write_text(str(p.pid), encoding="utf-8")
    for _ in range(60):
        time.sleep(0.5)
        if p.poll() is not None:
            tail = TUNNEL_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
            TUNNEL_PID.unlink(missing_ok=True)
            fail("터널이 시작되지 않았습니다. 로그:\n" + "\n".join(tail))
        u = tunnel_url()
        if u.startswith("https://"):
            say(f"[완료] 외부 주소: {u}")
            say("  이 주소로 팀원이 접속합니다. 로그인한 사용자만 쓸 수 있습니다. 끄기: python manage.py tunnel stop")
            if not token:
                say("  (임시 주소라 터널을 다시 켜면 바뀝니다. 고정 주소는 README의 '외부 접속' 참고)")
            return
        if token and "Registered tunnel connection" in TUNNEL_LOG.read_text(encoding="utf-8", errors="replace"):
            say("[완료] Cloudflare 터널에 연결했습니다. 주소: Cloudflare에서 지정한 도메인 (.env의 PUBLIC_URL에 적어 두면 여기 표시)")
            return
    say("[주의] 터널은 켰지만 주소를 아직 확인하지 못했습니다. python manage.py tunnel status 로 확인하세요.")


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
        # 메모리에 올라온 건 pull 이전 코드이므로, 새로 받은 manage.py로 재시작한다
        r = subprocess.run([sys.executable, str(ROOT / "manage.py"), "restart", "--port", str(args.port)])
        if r.returncode:
            fail("재시작 실패 — python manage.py logs 60으로 원인을 확인하세요.")
    say("[완료] 업데이트가 끝났습니다.")


def main() -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    common.add_argument("--host", default=None, help="0.0.0.0이면 같은 망에서 IP로 접속 (기본: .env의 HOST 또는 127.0.0.1)")
    ap = argparse.ArgumentParser(description="제안서 루틴 도우미 관리")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("setup", "doctor", "start", "serve", "stop", "restart", "status", "update"):
        sub.add_parser(name, parents=[common])
    lp = sub.add_parser("logs", parents=[common])
    lp.add_argument("n", nargs="?", type=int, default=40)
    tp = sub.add_parser("tunnel", parents=[common])
    tp.add_argument("action", nargs="?", choices=["start", "stop", "status"], default="status")
    args = ap.parse_args()
    os.chdir(ROOT)
    globals()[f"cmd_{args.cmd}"](args)


if __name__ == "__main__":
    main()
