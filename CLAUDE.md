# 제안서 루틴 도우미 — Claude Code 안내

입찰 제안서 작성 루틴(RFP 비교 분석 → 공고서 체크리스트 → 핵심 추진 전략 → 목차 PPT)을 자동화하는 로컬 웹앱.
FastAPI + Jinja2 서버 렌더링 + 바닐라 JS. 사용자는 한국어를 쓰며 UI·문서·커밋 메시지도 한국어로 쓴다.
기능 명세: `docs/요구사항명세서.md` (기능을 바꾸면 해당 FR 항목도 같이 고친다).

## 실행·관리 — 항상 manage.py 사용
`manage.py`는 비대화식이며 어떤 Python 3.9+로 실행해도 된다(앱은 `~/.proposal-helper/venv`에서 돈다).
Windows에서 `python`이 없으면 `py manage.py ...`.

| 할 일 | 명령 |
|---|---|
| 최초 설치 | `python manage.py setup` → `python manage.py doctor` |
| 서버 시작(백그라운드) | `python manage.py start` → http://127.0.0.1:8000 |
| 코드 수정 후 반영 | `python manage.py restart` (uvicorn --reload를 쓰지 않으므로 재시작 필요) |
| 상태 / 로그 | `python manage.py status`, `python manage.py logs 80` |
| 종료 | `python manage.py stop` |
| 최신 코드 받기 | `python manage.py update` |

- `run.bat`/`setup.bat`/`update.bat`은 사람이 더블클릭하는 용도. 에이전트는 manage.py를 쓴다(run.bat은 창을 붙잡는다).
- 화면 변경 후 브라우저 캐시 때문에 안 보이면 사용자에게 Ctrl+F5를 안내한다(서버는 no-cache 헤더를 보낸다).
- Claude 연결: `.env`의 `ANTHROPIC_API_KEY`(API 방식) 또는 Claude Code CLI 로그인(Pro/Max 구독, `claude auth login`).
  API 키·비밀번호는 사용자가 직접 입력한다. 대신 입력하지 않는다.

## 구조
```
app/main.py            라우트(페이지 + /api/*), 전략 백그라운드 작업 관리
app/analysis.py        단계별 Claude 프롬프트·JSON 스키마 (RFP 비교, 체크리스트, 전략+배점표, 목차)
app/claude_client.py   call_json(): API(anthropic SDK) 또는 CLI(claude -p --json-schema) 백엔드
app/extractors.py      PDF/DOCX/HWPX/HWP/TXT 텍스트 추출
app/pptx_*.py          PPT 생성 — pptx_token(노트 마커 #표지 등 + {{토큰}}) > pptx_form(힌트 텍스트) > 레이아웃
app/template_struct.py 임의 PPT → 토큰형 양식 자동 구조화 (Claude 비전)
app/thumbs.py, render.ps1  PowerPoint COM으로 슬라이드 PNG (Windows+PowerPoint 전용, 실패 시 생략)
app/company.py         우리 회사 현황(data/company.json) — 전략 추천에 반영
app/templates/*.html   화면. 각 페이지 스크립트는 <script type="text/x-page"> (app.js 라우터가 실행)
app/static/app.js      프레임식 라우터(전체 새로고침 없음), api()/toast()/runLong() 헬퍼
data/                  런타임 데이터. git에는 data/templates/만 포함
```

## 주의
- `.bat` 파일은 **CP949 + CRLF, chcp 없이** 저장해야 cmd에서 한글이 깨지지 않는다(.gitattributes에서 -text).
- 이 폴더가 Google 드라이브 동기화 폴더일 수 있어 venv는 사용자 홈에 둔다.
- Jinja에서 `p.step2.items`는 dict.items로 해석된다 → `p.step2['items']`.
- 템플릿 안의 `{{토큰}}` 설명은 `{% raw %}`로 감싼다.
- 커밋하지 않는 것: `.env`, `data/projects/`, `data/company.json`, `data/settings.json` (회사·고객 정보).
