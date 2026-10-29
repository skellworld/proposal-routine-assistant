---
description: 이 PC에 제안서 루틴 도우미를 설치하고 점검한 뒤 서버를 켠다
---
제안서 루틴 도우미를 이 PC에 설치해 줘. 순서:

1. Python 3.11 이상이 있는지 확인한다(`python --version`, Windows는 `py --version`도). 없으면 설치 방법을 안내하고, 사용자가 동의하면 `winget install Python.Python.3.12`로 설치한다.
2. `python manage.py setup` 실행.
3. `python manage.py doctor` 실행.
   - Claude 연결이 준비되지 않았다고 나오면 두 방법을 설명하고 사용자가 고르게 한다.
     - Pro/Max 계정: Claude Code CLI가 있으면 사용자가 터미널에서 `claude auth login`을 직접 실행하도록 안내.
     - API 키: 사용자가 `.env`에 `ANTHROPIC_API_KEY=`를 직접 입력하도록 안내. 키를 대신 입력하지 않는다.
   - 사용자가 준비했다고 하면 doctor를 다시 실행해 확인한다.
4. `python manage.py start`로 서버를 켜고 주소(http://127.0.0.1:8000)를 알려 준다.
5. 새 PC라면 왼쪽 바 **🏢 우리 회사 현황**에 회사 소개를 붙여 넣어야 한다고 안내한다(저장소에 포함되지 않음).

$ARGUMENTS
