---
description: GitHub에서 최신 코드를 받아 패키지를 갱신하고 서버를 재시작한다
---
1. `git status --short`로 이 PC에서 고친 파일이 있는지 본다. 있으면 무엇인지 보여 주고, 먼저 커밋할지(`/publish`) 되돌릴지 사용자에게 묻는다. 임의로 버리지 않는다.
2. `python manage.py update` 실행 (git pull → 패키지 갱신 → 실행 중이면 재시작).
3. `git log --oneline -5`로 받은 변경 내역을 한국어로 짧게 요약해 준다.

$ARGUMENTS
