#!/usr/bin/env bash
# macOS / Linux 실행 스크립트 (Windows는 run.bat)
cd "$(dirname "$0")"
VENV="$HOME/.proposal-helper/venv"
if [ ! -x "$VENV/bin/python" ]; then echo "먼저 ./setup.sh 를 실행하세요."; exit 1; fi
URL="http://127.0.0.1:8000"
( sleep 2; (command -v open >/dev/null && open "$URL") || (command -v xdg-open >/dev/null && xdg-open "$URL") ) >/dev/null 2>&1 &
echo "제안서 루틴 도우미 실행 중 - Ctrl+C로 종료합니다."
exec "$VENV/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
