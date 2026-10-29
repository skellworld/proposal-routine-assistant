---
description: 이 PC의 코드·템플릿 변경을 커밋하고 GitHub에 올려 다른 PC와 공유한다
---
1. `git status --short`와 `git diff --stat`으로 변경 파일을 확인한다.
2. 올라가면 안 되는 파일이 없는지 확인한다: `.env`, `data/projects/`, `data/company.json`, `data/settings.json`(회사·고객 정보). .gitignore로 막혀 있어야 정상이며, 보이면 커밋하지 말고 알린다.
3. 변경 내용을 한국어로 요약한 커밋 메시지를 만들어 사용자에게 보여 주고, 커밋·푸시해도 되는지 확인받는다.
4. 승인되면 `git add -A` → `git commit` → `git push`. 푸시가 거절되면(원격이 앞섬) `git pull --rebase` 후 다시 푸시한다.
5. 다른 PC에서는 `/update`(또는 update.bat)로 받으면 된다고 안내한다.

$ARGUMENTS
