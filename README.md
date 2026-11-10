# 제안서 루틴 도우미 (로컬 웹)

RFP 비교 분석 → 공고서 분석·준비서류 체크리스트 → 핵심 추진 전략 추천 → 템플릿 기반 목차 PPT 생성을 자동화하는 로컬 웹사이트입니다.
요구사항은 [docs/요구사항명세서.md](docs/요구사항명세서.md)를 참고하세요.

## 다른 PC에 설치하기 (Windows 기준)

### 1. 필요한 프로그램 (최초 1회)
PowerShell에서 실행합니다.
```powershell
winget install Python.Python.3.12
winget install Git.Git
winget install GitHub.cli
```
설치 후 PowerShell 창을 새로 열어야 명령이 인식됩니다.

### 2. 코드 받기
비공개(Private) 저장소이므로 GitHub 로그인이 필요합니다. 저장소에 접근 권한이 있는 계정으로 로그인하세요.
```powershell
gh auth login
gh repo clone <GitHub계정>/proposal-routine-assistant
cd proposal-routine-assistant
```

### 3. 설치
`setup.bat` 더블클릭 — 가상환경(`%USERPROFILE%\.proposal-helper\venv`)을 만들고 패키지를 설치합니다.

### 4. 실행 · 최초 설정
`run.bat` 더블클릭 → 브라우저에서 http://127.0.0.1:8000 이 열립니다. 서버 창(검은 창)을 닫으면 종료됩니다.

처음 열면 **관리자 계정 만들기** 화면이 나옵니다(보안상 서버 PC에서 직접 접속할 때만 만들 수 있습니다).
계정을 만들면 **시작하기** 화면에서 분석에 쓸 AI를 연결합니다. 연결 테스트가 성공해야 다른 메뉴가 열립니다.

| AI 연결 | 설명 |
|---|---|
| Claude (Anthropic API 키) | https://console.anthropic.com/settings/keys 에서 발급. 사용한 만큼 본인 계정 과금 |
| ChatGPT (OpenAI API 키) | https://platform.openai.com/api-keys 에서 발급. 사용한 만큼 본인 계정 과금 |
| 내 PC의 Claude Code (Pro·Max 구독) | 본인 PC에서 실행기를 켜 두면 본인 Claude Code로 분석. 결과는 팀 워크스페이스에 공유 (아래 참고) |
| 서버 PC의 Pro 계정 (Claude Code) | 서버 PC에서 직접 접속할 때만 선택 가능. `irm https://claude.ai/install.ps1 \| iex` 설치 후 `claude auth login` |

**내 PC의 Claude Code로 분석하기 (팀원)**
1. 내 PC에 Python 3.9+와 Claude Code를 설치하고 `claude auth login`으로 로그인합니다.
2. 웹 `⚙ 내 AI 연결` → '내 PC의 Claude Code' → `proposal-worker.py` 내려받기 → [토큰 발급].
3. 화면에 나온 명령(`python proposal-worker.py --server <서버주소> --token <토큰>`)을 실행하고 창을 켜 둡니다. 다음부터는 `python proposal-worker.py`만 실행하면 됩니다.
4. 설정 화면에 '● 연결됨'이 보이면 [저장하고 연결 테스트]. 이후 내가 실행한 분석은 내 PC에서 처리되고 결과는 팀이 함께 봅니다.
   실행기는 서버로 나가는 연결만 쓰므로 내 PC의 방화벽·포트를 열 필요가 없습니다. 분석하는 동안 실행기가 켜져 있어야 합니다.

Claude Pro·ChatGPT Plus 같은 채팅 구독은 API와 결제가 별개라 팀원 분석에는 쓸 수 없습니다(각 API 콘솔에서 결제 수단 등록 필요).
키는 서버에 암호화해 저장되며, 각자 실행한 분석에만 쓰입니다.

### 5. 팀원이 외부에서 쓰게 하기 (Cloudflare Tunnel)
서버 PC에서 앱 서버를 켠 채로 터널을 엽니다. 공유기 포트를 열 필요가 없고 https 주소가 발급됩니다.
```powershell
winget install Cloudflare.cloudflared   # 최초 1회
python manage.py start
python manage.py tunnel start           # → https://xxxx.trycloudflare.com 주소 출력
python manage.py tunnel stop            # 외부 접속 끄기
```
1. 사이드바 **👥 사용자 관리**에서 팀원 계정을 만들고, 표시된 임시 비밀번호와 외부 주소를 팀원에게 전달합니다.
2. 팀원은 처음 로그인할 때 비밀번호를 바꾸고 본인 Claude 또는 ChatGPT API 키를 연결합니다.
3. 워크스페이스·템플릿·우리 회사 현황은 팀이 함께 봅니다.

- 임시 주소(trycloudflare.com)는 터널을 다시 켤 때마다 바뀝니다. **고정 주소**가 필요하면 Cloudflare 계정에 도메인을 연결하고
  Zero Trust → Networks → Tunnels에서 터널을 만든 뒤(서비스 주소 `http://127.0.0.1:8000`), 발급된 토큰을 `.env`의 `CLOUDFLARE_TUNNEL_TOKEN=`에, 주소를 `PUBLIC_URL=`에 적습니다.
- 서버 PC가 꺼지거나 절전에 들어가면 외부에서도 접속되지 않습니다.

### 최신 버전으로 업데이트
`update.bat` 더블클릭 — `git pull`로 최신 코드를 받고 패키지를 맞춥니다. 이후 `run.bat`을 다시 실행하세요.

### Claude Code로 설치·실행하기
저장소 폴더에서 Claude Code를 열면 `CLAUDE.md`(프로젝트 안내)를 자동으로 읽습니다. 아래 명령을 입력하면 됩니다.

| 명령 | 하는 일 |
|---|---|
| `/install` | Python 확인 → 설치 → Claude 연결 점검 → 서버 실행 |
| `/start` | 서버 켜기 (켜져 있으면 재시작해 최신 코드 반영), `/start 종료`로 끄기 |
| `/update` | GitHub에서 최신 코드 받기 → 패키지 갱신 → 재시작 |
| `/publish` | 이 PC의 코드·템플릿 변경을 커밋해 GitHub에 올리기 (확인 후 진행) |

말로 "서버 켜줘", "업데이트해줘"라고 해도 됩니다. 터미널에서 직접 쓰려면 같은 기능의 `manage.py`를 쓰세요.
```powershell
python manage.py setup     # 설치
python manage.py doctor    # 설치·Claude 연결 점검
python manage.py start     # 백그라운드 실행 (stop / restart / status / logs / update)
python manage.py tunnel start   # 외부 접속 주소 열기 (stop / status)
```

### macOS / Linux
`./setup.sh` → `./run.sh` (업데이트는 `git pull` 후 `./setup.sh`). 템플릿 미리보기 이미지는 PowerPoint가 있는 Windows에서만 만들어지며, 그 외 기능은 동일합니다.

## 저장소에 포함되는 것 / 포함되지 않는 것
| 포함 | 포함하지 않음 (PC마다 따로) |
|---|---|
| 코드·화면·설치 스크립트·명세서 | `.env` (터널 토큰 등) |
| 회사 PPT 템플릿 (`data/templates/`) | 워크스페이스·업로드 문서·결과물 (`data/projects/`) |
| | 우리 회사 현황 (`data/company.json`) |
| | 계정·암호화된 API 키 (`data/users.json`), 서버 비밀키 (`data/secret.key`) |

- 새 PC에서는 **🏢 우리 회사 현황**을 다시 입력하세요(기존 PC 화면에서 복사해 붙여 넣으면 됩니다).
- 템플릿을 새로 올리거나 고친 뒤 다른 PC와 공유하려면 아래처럼 커밋·푸시합니다.
  ```powershell
  git add data/templates
  git commit -m "템플릿 업데이트"
  git push
  ```

## 사용 흐름
화면은 **왼쪽 사이드바(워크스페이스 목록 · 템플릿 관리 · 우리 회사 현황) + 본문** 구조이며, 메뉴 이동 시 페이지 전체를 새로고침하지 않습니다.

0. **템플릿 관리**: 회사 템플릿(.pptx) 등록 → 자동 설명·자동 구조화(양식화), 양식 다운로드
0. **우리 회사 현황**: 회사 소개를 자유 형식으로 입력 → 추진 전략 추천에 반영
1. **+ 새 워크스페이스**: 제안 과업명·발주기관·마감일 입력 → 사이드바에 D-day와 진행 상태(●●●●) 표시
2. **1. RFP 분석**: 현재/과거 RFP·공고서를 한꺼번에 드래그 → [분석 실행] → 비교표 웹/PNG 확인
3. **2. 공고서·체크리스트**: 1단계에서 공고서를 함께 올리면 자동 생성 → 구비 시 체크 (자동 저장)
4. **3. 추진 전략**: 핵심 전략 5가지·평가 배점표·우리 회사 강점/보완점 (자동 생성)
5. **4. 목차 PPT**: 템플릿 선택 → [RFP에서 목차 추출] → 편집 → [PPT 생성]

## 폴더 구조
```
app/            서버 코드 (FastAPI) · templates(화면) · static(CSS/JS)
data/projects/  워크스페이스별 업로드 파일·분석 결과·생성 PPT   (git 제외)
data/templates/ PPT 템플릿 + templates.json(이름·설명·양식 정보)  (git 포함)
docs/           요구사항 명세서
```

## 참고
- 지원 문서: PDF, HWP(5.0), HWPX, DOCX, TXT. 암호화/배포용 HWP는 PDF로 변환해 업로드하세요.
- 텍스트가 없는 스캔 PDF는 원본을 Claude에 직접 전달해 분석합니다.
- 변경 사항이 화면에 안 보이면 브라우저에서 **Ctrl+F5**로 새로고침하세요.
