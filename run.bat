@echo off
cd /d "%~dp0"
set "VENV=%USERPROFILE%\.proposal-helper\venv"
if not exist "%VENV%\Scripts\python.exe" (echo 먼저 setup.bat을 실행하세요. & pause & exit /b 1)
rem 이미 실행 중이면 브라우저만 연다
netstat -ano | findstr /R /C:"127.0.0.1:8000 .*LISTENING" >nul && (start "" http://127.0.0.1:8000 & exit /b 0)
start "" http://127.0.0.1:8000
echo 제안서 루틴 도우미 실행 중 - 이 창을 닫으면 서버가 종료됩니다.
"%VENV%\Scripts\python" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
