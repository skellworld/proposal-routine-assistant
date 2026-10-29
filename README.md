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

### 4. Claude 연결 (둘 중 하나, 사이드바 **⚙ 설정**에서 선택·연결 테스트)
- **Pro 계정 (Claude Code)** — 추가 비용 없음, Pro 사용량 한도 공유
  - 설치: PowerShell에서 `irm https://claude.ai/install.ps1 | iex`
  - 로그인: `& "$env:USERPROFILE\.local\bin\claude.exe" auth login` → 브라우저에서 승인
- **API 키** — 사용량만큼 과금: `.env` 파일에 `ANTHROPIC_API_KEY=` 입력

### 5. 실행
`run.bat` 더블클릭 → 브라우저에서 http://127.0.0.1:8000 이 열립니다. 서버 창(검은 창)을 닫으면 종료됩니다.

### 최신 버전으로 업데이트
`update.bat` 더블클릭 — `git pull`로 최신 코드를 받고 패키지를 맞춥니다. 이후 `run.bat`을 다시 실행하세요.

### macOS / Linux
`./setup.sh` → `./run.sh` (업데이트는 `git pull` 후 `./setup.sh`). 템플릿 미리보기 이미지는 PowerPoint가 있는 Windows에서만 만들어지며, 그 외 기능은 동일합니다.

## 저장소에 포함되는 것 / 포함되지 않는 것
| 포함 | 포함하지 않음 (PC마다 따로) |
|---|---|
| 코드·화면·설치 스크립트·명세서 | `.env` (API 키) |
| 회사 PPT 템플릿 (`data/templates/`) | 워크스페이스·업로드 문서·결과물 (`data/projects/`) |
| | 우리 회사 현황 (`data/company.json`), 설정 (`data/settings.json`) |

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
