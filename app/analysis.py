"""단계별 Claude 프롬프트와 출력 스키마."""
from pathlib import Path

from .claude_client import call_json, document_blocks


def _obj(props: dict) -> dict:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


_STR = {"type": "string"}

# ---------- 단계 1: RFP 비교 분석 ----------
COMPARE_ITEMS = [
    "사업명", "발주기관", "사업기간", "사업예산", "사업 목적/배경", "주요 과업범위",
    "기술/보안 요구사항", "투입인력 요건", "평가기준(기술:가격 비율)", "기술평가 세부 배점",
    "제출서류", "제안서 분량/형식", "주요 일정(공고·마감·발표)", "기타 특이사항",
]
# 공고서가 있을 때 추가로 비교하는 항목
NOTICE_ITEMS = [
    "입찰 참가자격", "계약·낙찰자 결정 방식", "공동수급·하도급 허용", "입찰보증금·계약보증",
    "제안서 제출 방법·장소", "제안설명회·질의 일정",
]
SOURCES = ["RFP", "공고서", "공통"]

STEP1_SCHEMA = _obj({
    "project_title": _STR,
    "summary": _STR,
    "key_changes": {"type": "array", "items": _STR},
    "strategy": {"type": "array", "items": _STR},
    "rows": {
        "type": "array",
        "items": _obj({
            "item": _STR,
            "source": {"type": "string", "enum": SOURCES},
            "past": _STR,
            "current": _STR,
            "change": {"type": "string", "enum": ["신규", "삭제", "변경", "동일", "확인필요"]},
            "insight": _STR,
        }),
    },
})

STEP1_SYSTEM = """당신은 공공·민간 사업 제안서 작성을 지원하는 RFP(제안요청서)·입찰공고서 분석 전문가입니다.
문서에 근거한 사실만 기재하고, 문서에 없는 내용은 "명시 없음"으로 적습니다. 추측하지 않습니다.
모든 출력은 한국어로 작성합니다. 표의 각 셀은 표 이미지로도 출력되므로 핵심만 120자 이내로 간결하게 씁니다.
가능하면 근거 문서와 페이지를 (공고 p.N) / (RFP p.N) 형태로 덧붙입니다."""


def analyze_rfp(current_rfp: list[Path], current_notice: list[Path],
                past_rfp: list[Path], past_notice: list[Path]) -> dict:
    content = (document_blocks(current_rfp, "현재 RFP") + document_blocks(current_notice, "현재 공고서")
               + document_blocks(past_rfp, "과거 RFP") + document_blocks(past_notice, "과거 공고서"))
    has_notice = bool(current_notice or past_notice)
    items = COMPARE_ITEMS + (NOTICE_ITEMS if has_notice else [])
    current_set = " + ".join(n for n, d in (("현재 RFP", current_rfp), ("현재 공고서", current_notice)) if d)
    past_set = " + ".join(n for n, d in (("과거 RFP", past_rfp), ("과거 공고서", past_notice)) if d)
    common = f"""
비교 항목(기본): {", ".join(items)}
- 기본 항목은 모두 포함하고, 문서에서 의미 있는 차이가 있는 항목이 더 있으면 추가하세요.
- RFP와 공고서는 한 묶음으로 봅니다. 같은 항목이 두 문서에 다르게 적혀 있으면 공고서 기준으로 쓰고 insight에 불일치를 지적하세요.
- source: 해당 항목의 주된 근거 문서 — {" / ".join(SOURCES)}
- insight: 제안서 작성 관점에서의 시사점(무엇을 강조·대비해야 하는지)
- strategy: 이번 제안에서 취해야 할 전략 포인트 3~5개
- summary: 현재 사업의 3~4문장 요약"""
    if past_set:
        task = f"""[현재 문서: {current_set}]와 [과거 문서: {past_set}]를 비교 분석하세요.
{common}
- past: 과거 문서 내용, current: 현재 문서 내용
- change: 신규(현재에만 있음) / 삭제(과거에만 있음) / 변경 / 동일 / 확인필요 중 하나
- key_changes: 가장 중요한 변경점 3~5개 (공고서상의 자격·계약방식 변경도 포함)"""
    else:
        task = f"""[현재 문서: {current_set}]를 분석하세요(비교 대상 과거 문서 없음).
{common}
- past는 모두 "-", change는 모두 "확인필요"로 두고 current에 내용을 정리하세요.
- key_changes: 사업의 핵심 요구사항 3~5개"""
    content.append({"type": "text", "text": task})
    return call_json(STEP1_SYSTEM, content, STEP1_SCHEMA)


# ---------- 단계 2: 공고서 체크리스트 ----------
CATEGORIES = ["입찰참가", "자격증빙", "제안서류", "가격서류", "기타"]

STEP2_SCHEMA = _obj({
    "overview": _obj({
        "notice_no": _STR,
        "bid_method": _STR,
        "qualifications": {"type": "array", "items": _STR},
        "joint_bid": _STR,
        "deposit": _STR,
        "cautions": {"type": "array", "items": _STR},
    }),
    "schedule": {"type": "array", "items": _obj({"label": _STR, "date": _STR})},
    "submission": _STR,
    "items": {
        "type": "array",
        "items": _obj({
            "category": {"type": "string", "enum": CATEGORIES},
            "name": _STR,
            "detail": _STR,
            "format": _STR,
            "issuer": _STR,
            "required": {"type": "boolean"},
            "source": _STR,
        }),
    },
})

STEP2_SYSTEM = """당신은 입찰 제안 실무 담당자를 돕는 공고서 검토 전문가입니다.
공고서/제안요청서를 읽고 제출 전에 실제로 준비(발급·작성·날인·출력)해야 하는 문서와 자료를 빠짐없이 목록화합니다.
문서에 근거한 내용만 기재하고 한국어로 작성합니다."""

STEP2_TASK = f"""위 공고서(또는 제안요청서)를 분석하고 준비서류 체크리스트를 만드세요.

- overview: 공고서 핵심 분석 (명시 없으면 "명시 없음")
  - notice_no: 공고번호
  - bid_method: 입찰·계약 방식 (예: 제한경쟁, 협상에 의한 계약)
  - qualifications: 입찰 참가자격 요건 목록
  - joint_bid: 공동수급·하도급 허용 여부와 조건
  - deposit: 입찰보증금·계약보증 조건
  - cautions: 놓치면 무효·감점이 될 수 있는 유의사항 (입찰 무효 사유, 서류 발급 기준일, 날인, 분량 제한 등)

- items: 실제 구비해야 하는 서류/자료 하나당 한 항목. 같은 서류를 중복하지 마세요.
  - category: {" / ".join(CATEGORIES)} 중 하나
  - name: 서류명 (예: 입찰참가신청서, 법인등기부등본, 정성제안서 PDF)
  - detail: 무엇을 어떻게 준비해야 하는지 (발급 기준일, 날인, 원본대조필 등 유의사항 포함)
  - format: 부수·형식 (예: 원본 1부, 사본 1부, 전자파일 PDF, USB 등). 명시 없으면 "명시 없음"
  - issuer: 발급처/작성주체 (예: 대법원 인터넷등기소, 자사 작성, 국세청 홈택스)
  - required: 필수 제출이면 true, 해당자만/선택이면 false
  - source: 근거 조항 또는 페이지 (예: 공고문 5.가항, p.12)
- schedule: 공고일, 설명회, 질의 마감, 제안서 제출 마감, 발표평가 등 주요 일정 (date는 문서 표기 그대로)
- submission: 제출 방법·장소 요약 (1~2문장)"""


def build_checklist(docs: list[Path]) -> dict:
    content = document_blocks(docs, "공고서")
    content.append({"type": "text", "text": STEP2_TASK})
    return call_json(STEP2_SYSTEM, content, STEP2_SCHEMA)


# ---------- 단계 3: 핵심 추진 전략 추천 ----------
STRATEGY_SCHEMA = _obj({
    "concept": _STR,
    "concept_desc": _STR,
    "strategies": {
        "type": "array",
        "items": _obj({
            "title": _STR,
            "message": _STR,
            "rationale": _STR,
            "actions": {"type": "array", "items": _STR},
            "evaluation": _STR,
            "where": _STR,
            "our_edge": _STR,
        }),
    },
    "gaps": {"type": "array", "items": _STR},
    "cautions": {"type": "array", "items": _STR},
    "scoring": _obj({
        "found": {"type": "boolean"},
        "source": _STR,
        "ratio": _STR,
        "total": _STR,
        "rows": {
            "type": "array",
            "items": _obj({
                "area": _STR,
                "item": _STR,
                "points": _STR,
                "criteria": _STR,
                "strategies": {"type": "array", "items": {"type": "integer"}},
            }),
        },
    }),
})

STRATEGY_SYSTEM = """당신은 공공·민간 입찰에서 다수 수주 경험이 있는 제안 전략가(Capture Manager)입니다.
RFP·공고서(및 과거 문서)를 근거로 이번 사업에서 평가위원에게 가장 설득력 있는 핵심 추진 전략을 제시합니다.
뻔한 일반론(예: '체계적 관리', '최고의 인력')은 피하고, 이 사업의 요구사항·평가 배점·전년 대비 변화에 직접 연결된 전략만 제시합니다.
문서에 없는 사실을 지어내지 않으며 한국어로 작성합니다."""

STRATEGY_TASK = """위 문서를 분석해 이 사업 제안서의 핵심 추진 전략 5가지를 추천하세요.

- concept: 제안 전체를 관통하는 제안 콘셉트(슬로건) 한 줄 (25자 이내)
- concept_desc: 콘셉트의 의미와 이유 2문장
- strategies: 정확히 5개, 중요도(배점 영향) 순
  - title: 전략명 (20자 이내, 명사형)
  - message: 제안서에 그대로 쓸 수 있는 핵심 메시지(거버닝 문장) 1문장
  - rationale: 이 전략이 필요한 근거 — RFP 요구사항·평가항목·배점·전년 대비 변화 등을 구체적으로, 근거 위치((RFP p.N)/(공고 §N)) 포함
  - actions: 제안서에 담을 구체적 실행 방안 3개 (각 1문장, 수치·방법론·산출물 등 구체적으로)
  - evaluation: 연계되는 평가항목과 배점 (명시 없으면 "명시 없음")
  - where: 제안서에서 이 전략을 강조할 위치 (RFP 작성 목차 기준 장·절 이름)
  - our_edge: [우리 회사 현황]에서 이 전략의 근거가 되는 강점·실적·인력·솔루션 (구체적으로 인용, 1~2문장). 회사 현황이 없거나 해당 내용이 없으면 ""
- gaps: [우리 회사 현황] 대비 RFP·공고 요구사항에서 부족하거나 불리한 점과 보완 방안 0~3개 (예: "모바일 진단 실적 부족 → 유사 실적 재구성·전문 인력 영입 계획 제시"). 회사 현황이 없으면 []
- cautions: 전략 수립 시 놓치면 안 되는 리스크·감점 요인 2~3개
- scoring: 현재 RFP·공고서의 (기술)평가 배점표
  - found: 문서에 배점표(평가항목별 배점)가 있으면 true. 없으면 false로 두고 나머지는 빈 값("", [])
  - source: 배점표의 근거 위치 (예: RFP p.32 [별표 2])
  - ratio: 기술:가격 평가 비율 (예: "기술 90 : 가격 10", 명시 없으면 "명시 없음")
  - total: 배점표 합계 (예: "100점")
  - rows: 배점표의 행을 문서 순서·표기 그대로 빠짐없이 옮깁니다. 가장 세부적인 평가항목 하나당 한 행
    - area: 평가부문(대분류, 예: 전략 및 방법론). 대분류가 없으면 ""
    - item: 평가항목(세부 항목명)
    - points: 배점 (예: "10", "5(정량)")
    - criteria: 평가기준·착안사항 요약 (80자 이내, 명시 없으면 "")
    - strategies: 이 항목을 공략하는 위 strategies의 번호(1~5) 목록. 해당 없으면 []"""


def build_strategy(current_rfp: list[Path], current_notice: list[Path],
                   past_rfp: list[Path], past_notice: list[Path], context: str = "",
                   company: str = "") -> dict:
    content = (document_blocks(current_rfp, "현재 RFP") + document_blocks(current_notice, "현재 공고서")
               + document_blocks(past_rfp, "과거 RFP") + document_blocks(past_notice, "과거 공고서"))
    task = STRATEGY_TASK
    if company:
        task = f"""[우리 회사 현황] — 이 제안서를 작성하는 우리 회사의 소개입니다.
<company_profile>
{company}
</company_profile>
전략은 일반론이 아니라 우리 회사가 실제로 가진 강점·실적·인력·솔루션으로 RFP 요구사항과 배점을 공략하는 방향으로 짜세요.
회사 현황에 없는 실적·인력·인증을 지어내지 말고, 부족한 부분은 gaps와 보완 방안으로 다루세요.
경쟁 구도에서 우리 회사가 유리한 지점이 드러나면 우선순위를 높이세요.

""" + task
    else:
        task = "[우리 회사 현황: 등록되지 않음 — our_edge는 \"\", gaps는 []로 두세요]\n\n" + task
    if context:
        task = f"[참고: 앞서 정리한 분석 결과]\n{context}\n\n" + task
    content.append({"type": "text", "text": task})
    return call_json(STRATEGY_SYSTEM, content, STRATEGY_SCHEMA)


# ---------- 단계 4: 제안서 목차 추출 ----------
STEP3_SCHEMA = _obj({
    "proposal_title": _STR,
    "toc": {
        "type": "array",
        "items": _obj({
            "level": {"type": "integer", "enum": [1, 2, 3]},
            "number": _STR,
            "title": _STR,
            "guide": _STR,
        }),
    },
})

STEP3_SYSTEM = """당신은 제안서 구성 전문가입니다. RFP에 명시된 '제안서 작성 목차(작성요령/작성지침)'를
원문 순서와 번호 체계를 그대로 살려 정확히 추출합니다. 한국어로 작성합니다."""

STEP3_TASK = """위 RFP에서 제안서(기술/정성 제안서) 작성 목차를 추출하세요.

- toc: 목차를 순서대로 평탄화한 목록. level 1=대목차, 2=중목차, 3=소목차
- number: 원문 번호 표기 (예: "Ⅰ", "1", "1.1", "가"). 없으면 빈 문자열
- title: 목차 제목 (원문 그대로)
- guide: 해당 목차에 작성해야 할 내용·지침 요약, 연계된 평가항목/배점이 있으면 함께 기재. 없으면 빈 문자열
- RFP에 작성 목차가 명시되지 않았다면 평가항목 구조를 바탕으로 표준 제안서 목차를 구성하고, guide 첫머리에 "[추천]"을 붙이세요.
- proposal_title: 표지와 머리말에 쓸 공식 사업명. 공고서/RFP에 적힌 사업명 그대로 쓰고 '제안서'라는 단어는 붙이지 마세요.
  25자를 넘으면 의미 단위로 줄바꿈(\\n)해 '주 사업명\\n세부 과정명'처럼 2줄로 나누세요."""


def extract_toc(docs: list[Path]) -> dict:
    content = document_blocks(docs, "현재 RFP")
    content.append({"type": "text", "text": STEP3_TASK})
    return call_json(STEP3_SYSTEM, content, STEP3_SCHEMA)
