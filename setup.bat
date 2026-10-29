@echo off
cd /d "%~dp0"
rem 가상환경은 Google 드라이브 등 동기화 폴더를 피하기 위해 사용자 폴더에 만든다
set "VENV=%USERPROFILE%\.proposal-helper\venv"
set "PY=python"
where py >nul 2>nul && set "PY=py"
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
echo [1/3] Python 가상환경 생성: %VENV%
"%PY%" -m venv "%VENV%" || (echo Python 3.11 이상을 먼저 설치하세요: winget install Python.Python.3.12 & pause & exit /b 1)
echo [2/3] 패키지 설치...
"%VENV%\Scripts\python" -m pip install --upgrade pip
"%VENV%\Scripts\python" -m pip install -r requirements.txt || (pause & exit /b 1)
echo [3/3] 설정 파일 준비...
if not exist .env copy .env.example .env >nul
echo.
echo 설치 완료. Claude 연결 방식 중 하나를 준비하세요 (자세한 내용은 README.md):
echo   (1) Pro 계정: Claude Code 설치 후 로그인 - 추가 비용 없음
echo   (2) API 키: .env 파일에 ANTHROPIC_API_KEY 입력
echo 그 다음 run.bat을 실행하세요.
pause