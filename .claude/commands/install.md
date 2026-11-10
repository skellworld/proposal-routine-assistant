---
description: 이 PC에 제안서 루틴 도우미를 설치하고 점검한 뒤 서버를 켠다
---
제안서 루틴 도우미를 이 PC에 설치해 줘. 순서:

1. Python 3.11 이상이 있는지 확인한다(`python --version`, Windows는 `py --version`도). 없으면 설치 방법을 안내하고, 사용자가 동의하면 `winget install Python.Python.3.12`로 설치한다.
2. `python manage.py setup` 실행.
3. `python manage.py doctor` 실행.
4. `python manage.py start`로 서버를 켜고 주소(http://127.0.0.1:8000)를 알려 준다.
5. 계정이 없으면, 사용자가 이 PC의 브라우저에서 접속해 **관리자 계정**을 직접 만들고 **시작하기** 화면에서 AI를 연결하도록 안내한다.
   - AI는 본인 Claude(Anthropic) 또는 ChatGPT(OpenAI) API 키, 또는 이 PC의 Pro 계정(`claude auth login`)을 쓸 수 있다.
   - 계정·비밀번호·API 키는 사용자가 직접 입력한다. 대신 입력하지 않는다.
6. 새 PC라면 왼쪽 바 **🏢 우리 회사 현황**에 회사 소개를 붙여 넣어야 한다고 안내한다(저장소에 포함되지 않음).
7. 팀원이 외부에서 쓰게 하려면 `winget install Cloudflare.cloudflared` 후 `python manage.py tunnel start`를 안내한다.
   공개 주소를 여는 것이므로 사용자가 원한다고 확인한 뒤에만 실행한다.

$ARGUMENTS
