"""임의의 제안서 PPT → 토큰형 양식 템플릿으로 자동 구조화.

1) 슬라이드 썸네일(PowerPoint가 있을 때)과 도형 구조(글자·위치·크기·글자 크기)를 뽑는다
2) Claude가 역할별 대표 슬라이드와 '어느 도형이 어느 토큰이 될지', 지울 예시 도형을 정한다
3) 그대로 적용해 #역할 노트 + {{토큰}}이 들어간 양식 파일을 만든다
"""
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.util import Emu

from . import thumbs
from .claude_client import ClaudeError, call_json, image_block
from .pptx_form import _finalize_order, _set_lines
from .pptx_token import ROLE_KO, ROLE_ORDER, TOKENS, describe_token_form

MAX_SLIDES = 40


# ---------------- 1) 구조 추출 ----------------
def _inch(v) -> float:
    return round(Emu(v or 0).inches, 2)


def _walk(shapes, prefix: str, sw, sh, out: list, depth=0):
    for i, s in enumerate(shapes):
        sid = f"{prefix}{i}"
        if s.left is not None and (s.left >= sw or s.top >= sh or s.left + s.width <= 0):
            continue  # 슬라이드 밖(작업 메모 등)
        text = s.text_frame.text.strip() if s.has_text_frame else ""
        kind = ("group" if s.shape_type == 6 else "placeholder" if s.is_placeholder else
                "picture" if s.shape_type == 13 else "table" if getattr(s, "has_table", False) and s.has_table else
                "text" if text else "shape")
        if kind == "shape" and depth > 0:
            continue
        sizes = [r.font.size.pt for p in (s.text_frame.paragraphs if s.has_text_frame else [])
                 for r in p.runs if r.font.size]
        rec = {"id": sid, "kind": kind, "box": [_inch(s.left), _inch(s.top), _inch(s.width), _inch(s.height)]}
        if text:
            rec["text"] = text.replace("\n", " ⏎ ")[:140]
            rec["lines"] = len([p for p in s.text_frame.paragraphs if p.text.strip()])
        if sizes:
            rec["pt"] = max(sizes)
        out.append(rec)
        if kind == "group":
            _walk(s.shapes, sid + ".", sw * 10, sh * 10, out, depth + 1)


def extract_outline(prs) -> dict:
    sw, sh = prs.slide_width, prs.slide_height
    slides, used_layouts = [], {}
    layouts = list(prs.slide_layouts)
    for n, s in enumerate(list(prs.slides)[:MAX_SLIDES], 1):
        recs = []
        _walk(s.shapes, f"s{n}:", sw, sh, recs)
        li = layouts.index(s.slide_layout) if s.slide_layout in layouts else -1
        used_layouts[li] = s.slide_layout
        slides.append({"slide": n, "layout": s.slide_layout.name, "shapes": recs})
    lay = []
    for li, lo in used_layouts.items():
        recs = []
        _walk(lo.shapes, f"L{li}:", sw, sh, recs)
        recs = [r for r in recs if r.get("text") and r["kind"] != "placeholder"]
        if recs:
            lay.append({"layout": lo.name, "shapes": recs})
    return {"slide_size_in": [_inch(sw), _inch(sh)], "slides": slides, "layouts": lay}


def _resolve(prs, sid: str):
    scope, path = sid.split(":", 1)
    shapes = (prs.slides[int(scope[1:]) - 1].shapes if scope.startswith("s")
              else prs.slide_layouts[int(scope[1:])].shapes)
    shape = None
    for idx in path.split("."):
        shape = list(shapes)[int(idx)]
        if shape.shape_type == 6:
            shapes = shape.shapes
    return shape


# ---------------- 썸네일 모음(콘택트 시트) ----------------
def contact_sheets(pngs: list[Path], out_dir: Path, per_sheet=12, cols=4, cell_w=420) -> list[Path]:
    sheets = []
    if not pngs:
        return sheets
    with Image.open(pngs[0]) as im0:
        cell_h = int(cell_w * im0.height / im0.width)
    try:
        font = ImageFont.truetype("malgunbd.ttf", 22)
    except OSError:
        font = ImageFont.load_default()
    for k in range(0, len(pngs), per_sheet):
        chunk = pngs[k:k + per_sheet]
        rows = (len(chunk) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * (cell_w + 10) + 10, rows * (cell_h + 10) + 10), "#888888")
        draw = ImageDraw.Draw(sheet)
        for j, p in enumerate(chunk):
            with Image.open(p) as im:
                im = im.convert("RGB").resize((cell_w, cell_h))
            x, y = 10 + (j % cols) * (cell_w + 10), 10 + (j // cols) * (cell_h + 10)
            sheet.paste(im, (x, y))
            draw.rectangle([x, y, x + 54, y + 30], fill="#d11")
            draw.text((x + 6, y + 2), f"s{k + j + 1}", fill="white", font=font)
        path = out_dir / f"sheet{k // per_sheet + 1}.png"
        sheet.save(path)
        sheets.append(path)
    return sheets


# ---------------- 2) Claude 매핑 ----------------
_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "roles": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "enum": ROLE_ORDER},
                "slide": {"type": "integer"},
                "fields": {"type": "array", "items": {
                    "type": "object", "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
                    "required": ["id", "text"], "additionalProperties": False}},
                "remove": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["role", "slide", "fields", "remove"], "additionalProperties": False}},
        "layout_fields": {"type": "array", "items": {
            "type": "object", "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
            "required": ["id", "text"], "additionalProperties": False}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "roles", "layout_fields", "warnings"],
    "additionalProperties": False,
}

_SYSTEM = """당신은 한국 공공·기업 제안서 PPT 디자인을 '자동 생성용 양식 템플릿'으로 바꾸는 전문가입니다.
주어진 PPT의 슬라이드 썸네일(sN 번호 표시)과 도형 구조(JSON)를 보고, 역할별 대표 슬라이드를 고르고
글자가 바뀌어야 할 도형을 {{토큰}}으로 지정합니다. 디자인 요소(배경, 로고, 장식, 머리말·꼬리말 틀, 쪽번호)는 건드리지 않습니다."""

_TASK = """아래 규칙으로 이 PPT를 양식 템플릿으로 구조화하세요.

[역할] 각 역할마다 가장 적합한 슬라이드 1장(slide=번호)을 고르세요. 해당 디자인이 없으면 그 역할은 빼세요.
- cover: 표지 / summary: 조견표·요구사항 대비표(있을 때만, 내용은 그대로 둠) / toc: 목차
- section: 대제목 간지(장 구분 페이지) / body: 일반 본문 페이지 / back: 뒷표지(감사합니다 등)
- body_mid: 중제목만 있는 본문용 디자인이 따로 있을 때만. 보통은 넣지 마세요.

[토큰] fields의 text에는 그 도형의 새 글자를 쓰고, 바뀔 부분을 토큰으로 표기합니다. 줄바꿈은 \\n.
사용 가능한 토큰: {tokens}
- cover/back: 사업명 도형 → 2줄(주제목/부제) 디자인이면 "{{{{사업명}}}}\\n{{{{세부사업명}}}}", 1줄이면 "{{{{사업명}}}}".
  날짜가 든 도형은 날짜 부분만 토큰으로, 예: "제안서  |  {{{{날짜}}}}". 회사명·로고·'제안서' 같은 고정 문구는 그대로.
- toc: 목차 항목이 여러 줄(행)로 반복되면 모든 행의 번호 도형 → "{{{{목차번호}}}}", 제목 도형 → "{{{{목차제목}}}}".
  번호와 제목이 한 텍스트 상자 한 줄에 있으면 "{{{{목차번호}}}} {{{{목차제목}}}}". 여러 줄 목록 상자면 첫 줄 하나만 남기는 의미로 그 상자 전체를 "{{{{목차번호}}}} {{{{목차제목}}}}"로.
- section: 장 번호 → "{{{{대번호}}}}", 장 제목 → "{{{{대제목}}}}", 하위 항목 목록 상자 → "{{{{중목차}}}}" (한 줄).
- body: 페이지 제목 영역 → 번호/제목이 따로면 "{{{{중번호}}}}"·"{{{{중제목}}}}", 한 상자면 "{{{{중번호}}}} {{{{중제목}}}}".
  소제목 띠 → "{{{{소번호}}}} {{{{소제목}}}}", 핵심 메시지(거버닝/헤드메시지) → "{{{{거버닝}}}}". 상위 장 표시가 있으면 "{{{{대번호}}}} {{{{대제목}}}}".
  remove에는 본문 영역의 예시 내용(예시 도식·표·예시 문장·예시 그림 그룹)의 id를 모두 넣으세요. 머리말·꼬리말·쪽번호·로고·장식은 넣지 마세요.
- layout_fields: 레이아웃(L로 시작하는 id)에 고정된 사업명 머리말 등이 있으면 토큰으로 지정(예: "{{{{사업명}}}}\\n{{{{세부사업명}}}}").
- 쪽번호의 전체 장수(예: '‹#› / 99')와 표지 날짜 외의 고정 문구는 자동 처리되거나 디자인이므로 지정하지 마세요.
- id는 반드시 아래 구조 JSON에 있는 값만 쓰세요. 그룹 안 글자는 그룹 자체가 아니라 안쪽 텍스트 도형 id를 지정하세요.
- summary: 이 템플릿 구성 한 줄 요약. warnings: 확신이 없거나 사용자가 확인할 점.

[도형 구조 JSON]
{outline}"""


def plan_structure(prs, work_dir: Path, pptx_path: Path) -> dict:
    outline = extract_outline(prs)
    pngs = thumbs.render(pptx_path, work_dir / "thumbs", width=840)
    content = [image_block(p, f"썸네일 모음 {i + 1}") for i, p in enumerate(contact_sheets(pngs[:MAX_SLIDES], work_dir))]
    text = _TASK.format(tokens=", ".join("{{" + t + "}}" for t in TOKENS),
                        outline=json.dumps(outline, ensure_ascii=False, separators=(",", ":")))
    if not content:
        text = "(썸네일 없음: 도형 구조만 보고 판단하세요.)\n" + text
    content.append({"type": "text", "text": text})
    return call_json(_SYSTEM, content, _SCHEMA)


# ---------------- 3) 적용 ----------------
def apply_structure(src: Path, plan: dict, out: Path) -> dict:
    prs = Presentation(str(src))
    slides = list(prs.slides)
    chosen, problems = {}, []
    for r in plan["roles"]:
        n = r["slide"]
        if r["role"] in chosen or not (1 <= n <= len(slides)) or n in chosen.values():
            problems.append(f"{ROLE_KO.get(r['role'], r['role'])}: 슬라이드 {n} 지정을 건너뜀")
            continue
        chosen[r["role"]] = n
    if "section" not in chosen and "body" not in chosen:
        raise ClaudeError("간지·본문 슬라이드를 찾지 못해 양식으로 만들 수 없습니다.")

    def set_text(sid, text):
        try:
            shape = _resolve(prs, sid)
        except (ValueError, IndexError):
            problems.append(f"없는 도형 id: {sid}")
            return
        if shape is None or not shape.has_text_frame:
            problems.append(f"글자를 넣을 수 없는 도형: {sid}")
            return
        _set_lines(shape.text_frame, text.split("\n"))
        if shape.width is not None and Emu(shape.width).inches < 1.2:
            shape.text_frame.word_wrap = False  # 번호용 좁은 상자에서 토큰이 세로로 꺾이지 않게

    for f in plan.get("layout_fields", []):
        set_text(f["id"], f["text"])
    for r in plan["roles"]:
        if chosen.get(r["role"]) != r["slide"]:
            continue
        for f in r["fields"]:
            if f["id"].startswith(f"s{r['slide']}:"):
                set_text(f["id"], f["text"])
        # 지울 도형은 마지막에 (id가 순서 기반이므로) 한꺼번에
        targets = []
        for sid in r["remove"]:
            if sid.startswith(f"s{r['slide']}:"):
                try:
                    targets.append(_resolve(prs, sid)._element)
                except (ValueError, IndexError, AttributeError):
                    problems.append(f"지울 도형을 찾지 못함: {sid}")
        for el in targets:
            if el.getparent() is not None:
                el.getparent().remove(el)
    ordered = []
    for role in ROLE_ORDER:
        if role in chosen:
            s = slides[chosen[role] - 1]
            s.notes_slide.notes_text_frame.text = "#" + ROLE_KO[role]
            ordered.append(s)
    _finalize_order(prs, ordered)
    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    info = describe_token_form(out)
    if info is None:
        raise ClaudeError("구조화 결과를 양식으로 인식하지 못했습니다.")
    return {"form": info, "summary": plan.get("summary", ""),
            "warnings": plan.get("warnings", []) + problems,
            "source_slides": {ROLE_KO[k]: v for k, v in chosen.items()}}


def structure(src: Path, out: Path, work_dir: Path) -> dict:
    work_dir.mkdir(parents=True, exist_ok=True)
    prs = Presentation(str(src))
    plan = plan_structure(prs, work_dir, src)
    (work_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    return apply_structure(src, plan, out)
