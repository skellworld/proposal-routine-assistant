#!/usr/bin/env bash
# macOS / Linux 설치 스크립트 (Windows는 setup.bat)
set -e
cd "$(dirname "$0")"
VENV="$HOME/.proposal-helper/venv"
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then echo "Python 3.11 이상을 먼저 설치하세요."; exit 1; fi
echo "[1/3] Python 가상환경 생성: $VENV"
"$PY" -m venv "$VENV"
echo "[2/3] 패키지 설치..."
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r requirements.txt
echo "[3/3] 설정 파일 준비..."
[ -f .env ] || cp .env.example .env
echo
echo "설치 완료. Claude 연결(Pro 계정 로그인 또는 .env의 ANTHROPIC_API_KEY)을 준비한 뒤 ./run.sh 를 실행하세요."
