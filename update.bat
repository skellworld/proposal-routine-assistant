@echo off
cd /d "%~dp0"
rem GitHub에서 최신 코드를 받고 패키지를 맞춘다
set "VENV=%USERPROFILE%\.proposal-helper\venv"
echo [1/2] 최신 코드 받기 (git pull)...
git pull || (echo git pull 실패 - 수정한 파일이 있거나 git이 설치되지 않았습니다. & pause & exit /b 1)
echo [2/2] 패키지 업데이트...
if not exist "%VENV%\Scripts\python.exe" (echo 먼저 setup.bat을 실행하세요. & pause & exit /b 1)
"%VENV%\Scripts\python" -m pip install -r requirements.txt || (pause & exit /b 1)
echo.
echo 업데이트 완료. 실행 중인 서버가 있으면 창을 닫고 run.bat을 다시 실행하세요.
pause