"""토큰형 양식 템플릿 기반 제안서 생성.

양식 파일 규칙 (템플릿 관리의 '양식 구조화'가 이 형식으로 만들어 준다)
- 슬라이드 노트 첫 줄에 역할 표시: #표지 #조견표 #목차 #간지 #본문 #본문-중 #뒷표지
- 바뀔 글자 자리에 {{토큰}}. 레이아웃(머리말 등)에 넣어도 된다.
  {{사업명}} {{세부사업명}} {{날짜}}            표지·뒷표지·머리말
  {{목차번호}} {{목차제목}}                     목차 — 이 토큰이 든 도형(그룹)을 대제목 수만큼 복제
  {{대번호}} {{대제목}} {{중목차}}              간지 — {{중목차}} 문단을 중제목 수만큼 복제
  {{대번호}} {{대제목}} {{중번호}} {{중제목}}
  {{소번호}} {{소제목}} {{거버닝}} {{작성지침}}  본문
"""
import copy
import re
from datetime import date
from pathlib import Path

from pptx import Presentation
from pptx.util import Emu, Inches

from .pptx_form import (_duplicate, _finalize_order, _iter_shapes, _num_dot, _renumber_ids,
                        _replace_total_pages, _set_para, chapter_tree, title_lines)

ROLE_MARKS = {"#표지": "cover", "#조견표": "summary", "#목차": "toc", "#간지": "section",
              "#본문-중": "body_mid", "#본문": "body", "#뒷표지": "back"}
ROLE_ORDER = ["cover", "summary", "toc", "section", "body", "body_mid", "back"]
ROLE_KO = {"cover": "표지", "summary": "조견표", "toc": "목차", "section": "간지", "body": "본문",
           "body_mid": "본문-중", "back": "뒷표지"}
TOKENS = ["사업명", "세부사업명", "날짜", "목차번호", "목차제목", "대번호", "대제목", "중목차",
          "중번호", "중제목", "소번호", "소제목", "거버닝", "작성지침"]
_TOKEN_RE = re.compile(r"\{\{\s*([^{}\s]+)\s*\}\}")


# ---------------- 인식 ----------------
def slide_role(slide) -> str | None:
    if not slide.has_notes_slide:
        return None
    first = (slide.notes_slide.notes_text_frame.text.strip().splitlines() or [""])[0].strip()
    for mark, role in ROLE_MARKS.items():  # '#본문-중'을 '#본문'보다 먼저 확인
        if first.startswith(mark):
            return role
    return None


def detect_token_form(prs) -> dict | None:
    roles = {}
    for i, s in enumerate(prs.slides):
        r = slide_role(s)
        if r and r not in roles:
            roles[r] = i
    if "section" not in roles and "body" not in roles:
        return None
    return roles


def _tokens_in(shapes) -> set:
    found = set()
    for sh in _iter_shapes(shapes):
        if sh.has_text_frame:
            found |= set(_TOKEN_RE.findall(sh.text_frame.text))
    return found


def describe_token_form(path: Path) -> dict | None:
    prs = Presentation(str(path))
    roles = detect_token_form(prs)
    if roles is None:
        return None
    slides = list(prs.slides)
    used = {ROLE_KO[r]: sorted(_tokens_in(slides[i].shapes)) for r, i in roles.items()}
    layout_tokens = sorted(set().union(*[_tokens_in(lo.shapes) for lo in prs.slide_layouts]))
    return {"kind": "token", "roles": {ROLE_KO[r]: i + 1 for r, i in roles.items()},
            "tokens": used, "layout_tokens": layout_tokens}


# ---------------- 채우기 ----------------
def _fill_paragraph(p, ctx: dict) -> bool:
    """문단의 토큰을 치환. 토큰만 있던 문단이 비게 되면 True."""
    text = "".join(r.text for r in p.runs)
    if "{{" not in text:
        return False
    new = _TOKEN_RE.sub(lambda m: str(ctx.get(m.group(1), "")), text)
    _set_para(p, new)
    return not new.strip()


def fill_tokens(shapes, ctx: dict) -> None:
    for sh in _iter_shapes(shapes):
        if not sh.has_text_frame:
            continue
        tf = sh.text_frame
        paras = list(tf.paragraphs)
        emptied = [p for p in paras if _fill_paragraph(p, ctx)]
        # 빈 문단이 된 줄(예: 세부사업명이 없을 때)은 지운다. 단, 마지막 1문단은 남긴다.
        for p in emptied:
            if len(tf.paragraphs) > 1:
                tf._txBody.remove(p._p)


def _expand_paragraph_list(shapes, token: str, values: list[str]) -> None:
    """{{token}}이 든 문단을 값 개수만큼 복제한다 (예: {{중목차}})."""
    for sh in _iter_shapes(shapes):
        if not sh.has_text_frame:
            continue
        for p in list(sh.text_frame.paragraphs):
            text = "".join(r.text for r in p.runs)
            if "{{" + token + "}}" not in text:
                continue
            anchor = p._p
            for v in values:
                dup = copy.deepcopy(anchor)
                anchor.addprevious(dup)
            anchor.getparent().remove(anchor)
            # 복제한 문단을 순서대로 채움
            body = sh.text_frame
            k = 0
            for q in body.paragraphs:
                t = "".join(r.text for r in q.runs)
                if "{{" + token + "}}" in t and k < len(values):
                    _set_para(q, t.replace("{{" + token + "}}", values[k]))
                    k += 1
            if not values and len(body.paragraphs) == 0:
                body.add_paragraph()
            return


def _expand_toc(slide, titles: list[str], slide_h: int) -> None:
    item_shapes = [s for s in slide.shapes if "목차제목" in _tokens_in([s])]
    if not item_shapes:
        return
    if len(item_shapes) == 1 and item_shapes[0].shape_type != 6:  # 텍스트 상자 하나 → 문단 복제
        sh = item_shapes[0]
        for p in list(sh.text_frame.paragraphs):
            if "{{목차제목}}" in "".join(r.text for r in p.runs):
                for i, t in enumerate(titles):
                    dup = copy.deepcopy(p._p)
                    p._p.addprevious(dup)
                p._p.getparent().remove(p._p)
                break
        i = 0
        for p in sh.text_frame.paragraphs:
            if "{{목차제목}}" in "".join(r.text for r in p.runs) and i < len(titles):
                _fill_paragraph(p, {"목차번호": f"{i + 1:02d}", "목차제목": titles[i]})
                i += 1
        return
    # 도형(그룹) 복제: 기존 묶음들의 간격·색 패턴을 이어간다
    items = sorted(item_shapes, key=lambda g: g.top)
    tree = slide.shapes._spTree
    top0 = items[0].top
    step = items[1].top - items[0].top if len(items) > 1 else int(items[0].height * 1.3)
    limit = slide_h - Inches(0.7)
    if top0 + step * len(titles) > limit:
        step = int((limit - top0) / max(len(titles), 1))
    pattern = items[:2]
    shapes = []
    for i in range(len(titles)):
        if i < len(items):
            shapes.append(items[i])
        else:
            dup = copy.deepcopy(pattern[i % len(pattern)]._element)
            tree.append(dup)
            shapes.append(next(s for s in slide.shapes if s._element is dup))
    for g in items[len(titles):]:
        tree.remove(g._element)
    for i, (g, t) in enumerate(zip(shapes, titles)):
        g.top = Emu(top0 + step * i)
        fill_tokens([g], {"목차번호": f"{i + 1:02d}", "목차제목": t})


def _set_notes(slide, text: str) -> None:
    slide.notes_slide.notes_text_frame.text = text or ""


def build_token_pptx(template: Path, toc: list[dict], title: str, out: Path, ym: str | None = None) -> Path:
    prs = Presentation(str(template))
    roles = detect_token_form(prs)
    if roles is None:
        raise ValueError("토큰형 양식 템플릿이 아닙니다.")
    src = list(prs.slides)
    lines = title_lines(title)
    base = {"사업명": lines[0], "세부사업명": " ".join(lines[1:]), "날짜": ym or f"{date.today():%Y.%m}"}
    chapters = chapter_tree(toc)
    ordered = []

    for lo in prs.slide_layouts:  # 머리말 등 레이아웃 토큰
        fill_tokens(lo.shapes, base)

    def use(role, ctx):
        s = src[roles[role]]
        fill_tokens(s.shapes, {**base, **ctx})
        _set_notes(s, "")
        ordered.append(s)

    if "cover" in roles:
        use("cover", {})
    if "summary" in roles:
        use("summary", {})
    if "toc" in roles:
        s = src[roles["toc"]]
        _expand_toc(s, [c["item"].get("title", "") for c in chapters], prs.slide_height)
        fill_tokens(s.shapes, base)
        _renumber_ids(s)
        _set_notes(s, "")
        ordered.append(s)

    for ci, ch in enumerate(chapters, 1):
        chap = {"대번호": f"{ci:02d}", "대제목": ch["item"].get("title", "")}
        if "section" in roles:
            sec = _duplicate(prs, src[roles["section"]])
            _expand_paragraph_list(sec.shapes, "중목차", [m["item"].get("title", "") for m in ch["mids"]])
            fill_tokens(sec.shapes, {**base, **chap})
            _renumber_ids(sec)
            guide = ch["item"].get("guide", "")
            _set_notes(sec, f"[작성 지침]\n{guide}" if guide else "")
            ordered.append(sec)
        if "body" not in roles:
            continue
        leaves = [(m, s) for m in ch["mids"] for s in (m["subs"] or [None])] or [(None, None)]
        for mid, sub in leaves:
            role = "body_mid" if sub is None and "body_mid" in roles else "body"
            body = _duplicate(prs, src[roles[role]])
            m_item = (mid or {}).get("item") or ch["item"]
            guide = (sub or m_item).get("guide", "")
            ctx = {**base, **chap,
                   "중번호": _num_dot(m_item.get("number", "")) if mid else f"{ci}.",
                   "중제목": m_item.get("title", ""),
                   "소번호": (sub or {}).get("number", ""), "소제목": (sub or {}).get("title", ""),
                   "거버닝": "", "작성지침": guide}
            fill_tokens(body.shapes, ctx)
            _renumber_ids(body)
            crumb = " > ".join(x for x in (chap["대제목"], ctx["중제목"] if mid else "", ctx["소제목"]) if x)
            _set_notes(body, f"[위치] {crumb}" + (f"\n[작성 지침]\n{guide}" if guide else ""))
            ordered.append(body)

    if "back" in roles:
        use("back", {})

    _finalize_order(prs, ordered)
    _replace_total_pages(prs, len(prs.slides))
    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    return out
