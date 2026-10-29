---
description: 제안서 루틴 도우미 서버를 켜거나(꺼져 있으면) 재시작한다
---
1. `python manage.py status`로 상태를 확인한다.
2. 꺼져 있으면 `python manage.py start`, 켜져 있으면 `python manage.py restart`로 최신 코드를 반영한다.
   (인자에 "stop"/"종료"가 있으면 `python manage.py stop`만 실행한다.)
3. 실패하면 `python manage.py logs 60`으로 원인을 확인해 설명하고, 가상환경이 없으면 `/install`을 안내한다.
4. 주소 http://127.0.0.1:8000 을 알려 주고, 화면이 예전 그대로면 Ctrl+F5를 누르라고 안내한다.

$ARGUMENTS
