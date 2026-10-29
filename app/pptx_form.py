"""양식형 템플릿 기반 제안서 생성.

템플릿의 예시 슬라이드를 '양식'으로 보고, 레이아웃 이름과 안내 문구(예: '대제목 입력', '중제목', '소제목 입력',
'거버닝')로 역할을 인식한 뒤, 디자인은 그대로 두고 글자만 바꿔 목차 구조대로 슬라이드를 복제한다.

인식하는 역할
- cover   : 레이아웃 이름에 '표지'가 들어간 첫 슬라이드 — 사업명(1~2줄), 'YYYY.MM' 날짜
- back    : 레이아웃 이름에 '표지'가 들어간 마지막 슬라이드(뒷표지) — 사업명
- summary : '조견표' 슬라이드 — 그대로 1장 포함
- toc     : '목차' 슬라이드 — 번호(01)+대제목 묶음(그룹)을 대제목 수만큼 늘리거나 줄임
- section : '간지' 슬라이드 — 번호, 대제목, 중제목 목록
- body    : '내지'/'본문' 슬라이드 — 중번호, 중제목, 거버닝(빈칸), 소제목. 예시 도형은 제거
레이아웃에 고정된 사업명(머리말)과 '‹#› / 99' 형태의 전체 쪽수도 바꾼다.
"""
import copy
import re
from datetime import date
from pathlib import Path

from pptx import Presentation
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches

R_ATTRS = (qn("r:embed"), qn("r:id"), qn("r:link"), qn("r:pict"))


# ---------------- 텍스트 유틸 ----------------
def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _set_para(p, text: str) -> None:
    """문단의 첫 글자 서식을 유지한 채 텍스트만 바꾼다."""
    for br in p._p.findall(qn("a:br")):
        p._p.remove(br)
    runs = p.runs
    if runs:
        runs[0].text = text
        for r in runs[1:]:
            r._r.getparent().remove(r._r)
    else:
        p.add_run().text = text


def _set_lines(text_frame, lines: list[str]) -> None:
    """여러 문단을 줄 목록으로 채운다. 문단이 모자라면 마지막 문단을 복제, 남으면 삭제."""
    paras = list(text_frame.paragraphs)
    txBody = text_frame._txBody
    lines = lines or [""]
    proto = paras[min(len(lines), len(paras)) - 1]._p
    for i, line in enumerate(lines):
        if i < len(paras):
            _set_para(paras[i], line)
        else:
            new_p = copy.deepcopy(proto)
            txBody.append(new_p)
            _set_para(text_frame.paragraphs[-1], line)
    for p in paras[len(lines):]:
        txBody.remove(p._p)


def _clear(shape) -> None:
    """개체 틀을 비워 레이아웃 안내 문구(편집 화면에서만 보임)가 나오게 한다."""
    tf = shape.text_frame
    for p in list(tf.paragraphs)[1:]:
        tf._txBody.remove(p._p)
    p = tf.paragraphs[0]
    for r in list(p._p):
        if r.tag in (qn("a:r"), qn("a:br"), qn("a:fld")):
            p._p.remove(r)


def _iter_shapes(shapes):
    for sh in shapes:
        yield sh
        if sh.shape_type == 6:  # group
            yield from _iter_shapes(sh.shapes)


def _renumber_ids(slide) -> None:
    for i, el in enumerate(slide.shapes._spTree.iter(qn("p:cNvPr")), start=2):
        el.set("id", str(i))


# ---------------- 슬라이드 조작 ----------------
def _duplicate(prs, src):
    """같은 프레젠테이션 안에서 슬라이드를 복제 (도형·이미지 관계 포함)."""
    new = prs.slides.add_slide(src.slide_layout)
    tree = new.shapes._spTree
    for el in list(tree)[2:]:
        tree.remove(el)
    rid_map = {}
    for rid, rel in src.part.rels.items():
        if rel.reltype in (RT.SLIDE_LAYOUT, RT.NOTES_SLIDE):
            continue
        if rel.is_external:
            rid_map[rid] = new.part.relate_to(rel.target_ref, rel.reltype, is_external=True)
        else:
            rid_map[rid] = new.part.relate_to(rel.target_part, rel.reltype)
    for el in list(src.shapes._spTree)[2:]:
        dup = copy.deepcopy(el)
        for node in dup.iter():
            for attr in R_ATTRS:
                if node.get(attr) in rid_map:
                    node.set(attr, rid_map[node.get(attr)])
        tree.append(dup)
    bg = src._element.cSld.find(qn("p:bg"))
    if bg is not None:
        new._element.cSld.insert(0, copy.deepcopy(bg))
    return new


def _slide_id(prs, slide):
    for sid in prs.slides._sldIdLst:
        if prs.part.related_part(sid.rId) is slide.part:
            return sid
    return None


def _finalize_order(prs, ordered: list) -> None:
    """ordered에 있는 슬라이드만 그 순서대로 남기고 나머지는 삭제."""
    lst = prs.slides._sldIdLst
    keep = [_slide_id(prs, s) for s in ordered]
    keep_set = {id(x) for x in keep}
    for sid in list(lst):
        if id(sid) not in keep_set:
            prs.part.drop_rel(sid.rId)
            lst.remove(sid)
    for sid in keep:
        lst.remove(sid)
        lst.append(sid)


def _set_notes(slide, text: str) -> None:
    if text:
        slide.notes_slide.notes_text_frame.text = text


# ---------------- 양식 인식 ----------------
def _ph_by_hint(slide_or_layout, tests: dict) -> dict:
    """안내 문구로 개체 틀 역할 찾기 → {역할: idx}"""
    found = {}
    for ph in slide_or_layout.placeholders:
        txt = ph.text_frame.text if ph.has_text_frame else ""
        for role, test in tests.items():
            if role not in found and test(txt):
                found[role] = ph.placeholder_format.idx
                break
    return found


_SECTION_TESTS = {
    "title": lambda t: "대제목" in t,
    "list": lambda t: "중제목" in t,
    "num": lambda t: bool(re.fullmatch(r"\s*\d{1,2}\s*", t)),
}
_BODY_TESTS = {
    "head": lambda t: "거버닝" in t or "헤드" in t,
    "sub": lambda t: "소제목" in t,
    "mid_title": lambda t: "중제목" in t,
    "mid_num": lambda t: bool(re.fullmatch(r"\s*\d{1,2}\.?\s*", t)),
}


def detect_form(prs) -> dict | None:
    """양식형 템플릿이면 역할별 슬라이드 번호(0부터)와 개체 틀 매핑을 반환, 아니면 None."""
    slides = list(prs.slides)
    by_layout = lambda *keys: [i for i, s in enumerate(slides) if any(k in s.slide_layout.name for k in keys)]
    covers, tocs = by_layout("표지"), by_layout("목차")
    sections, bodies, summaries = by_layout("간지"), by_layout("내지", "본문"), by_layout("조견표")
    if not (sections and bodies):
        return None
    form = {"section": sections[0], "body": bodies[0]}
    # 레이아웃의 안내 문구를 기준으로 하고, 슬라이드에 적힌 예시 문구로 보완
    sec = _ph_by_hint(slides[sections[0]].slide_layout, _SECTION_TESTS)
    sec.update(_ph_by_hint(slides[sections[0]], _SECTION_TESTS))
    body = _ph_by_hint(slides[bodies[0]].slide_layout, _BODY_TESTS)
    body.update(_ph_by_hint(slides[bodies[0]], _BODY_TESTS))
    if "title" not in sec or not ({"mid_title", "sub"} & set(body)):
        return None
    form["section_ph"], form["body_ph"] = sec, body
    if covers:
        form["cover"] = covers[0]
        if len(covers) > 1:
            form["back"] = covers[-1]
    if tocs:
        form["toc"] = tocs[0]
    if summaries:
        form["summary"] = summaries[0]
    # 템플릿에 들어 있는 기존 사업명(표지 첫 개체 틀) — 머리말 치환 기준
    if covers:
        ph = next((p for p in slides[covers[0]].placeholders if p.has_text_frame and p.text_frame.text.strip()), None)
        form["sample_title"] = ph.text_frame.text if ph is not None else ""
    return form


def describe_form(path: Path) -> dict | None:
    prs = Presentation(str(path))
    form = detect_form(prs)
    if form is None:
        return None
    names = {"cover": "표지", "summary": "조견표", "toc": "목차", "section": "간지", "body": "본문", "back": "뒷표지"}
    return {
        "roles": {names[k]: form[k] + 1 for k in names if k in form},
        "body_fields": sorted(form["body_ph"]),
        "section_fields": sorted(form["section_ph"]),
    }


# ---------------- 채우기 ----------------
def _fill_title_ph(slide, lines: list[str]) -> None:
    ph = next((p for p in slide.placeholders if p.has_text_frame and p.text_frame.text.strip()), None)
    if ph is not None:
        _set_lines(ph.text_frame, lines)


def _replace_date(slide, ym: str) -> None:
    for sh in _iter_shapes(slide.shapes):
        if sh.has_text_frame and not sh.is_placeholder:
            for p in sh.text_frame.paragraphs:
                for r in p.runs:
                    if re.search(r"\d{4}\.\d{1,2}", r.text):
                        r.text = re.sub(r"\d{4}\.\d{1,2}", ym, r.text)


def _replace_header_title(prs, sample: str, lines: list[str]) -> None:
    """레이아웃·슬라이드에 고정된 사업명(머리말)을 새 사업명으로."""
    if not _norm(sample):
        return
    targets = [lo.shapes for lo in prs.slide_layouts] + [s.shapes for s in prs.slides]
    for shapes in targets:
        for sh in _iter_shapes(shapes):
            if sh.has_text_frame and not sh.is_placeholder and _norm(sh.text_frame.text) == _norm(sample):
                _set_lines(sh.text_frame, lines)


def _replace_total_pages(prs, total: int) -> None:
    """'‹#› / 99' 처럼 쪽번호 필드 뒤의 전체 쪽수를 실제 값으로."""
    for lo in prs.slide_layouts:
        for sh in _iter_shapes(lo.shapes):
            if not sh.has_text_frame or "a:fld" not in sh._element.xml:
                continue
            for p in sh.text_frame.paragraphs:
                for r in p.runs:
                    if re.fullmatch(r"\s*/\s*\d+\s*", r.text) or re.fullmatch(r"\s*\d{2,}\s*", r.text):
                        r.text = re.sub(r"\d+", str(total), r.text)


def _fill_toc(slide, chapters: list[str], slide_h: int) -> None:
    """'01 + 대제목' 그룹을 대제목 수에 맞게 복제/삭제하고 세로 간격을 맞춘다."""
    def is_item(g):
        texts = [s.text_frame.text.strip() for s in _iter_shapes(g.shapes) if s.has_text_frame]
        return any(re.fullmatch(r"\d{1,2}", t) for t in texts) and any(t and not t.isdigit() for t in texts)

    groups = sorted([s for s in slide.shapes if s.shape_type == 6 and is_item(s)], key=lambda g: g.top)
    if not groups:
        return
    tree = slide.shapes._spTree
    top0 = groups[0].top
    step = groups[1].top - groups[0].top if len(groups) > 1 else Inches(0.9)
    bottom_limit = slide_h - Inches(0.7)
    n = len(chapters)
    if top0 + step * n > bottom_limit:
        step = int((bottom_limit - top0) / max(n, 1))
    pattern = groups[:2] if len(groups) >= 2 else groups
    items = []
    for i in range(n):
        if i < len(groups):
            items.append(groups[i])
        else:
            dup = copy.deepcopy(pattern[i % len(pattern)]._element)
            tree.append(dup)
            items.append(next(s for s in slide.shapes if s._element is dup))
    for g in groups[n:]:
        tree.remove(g._element)
    for i, (g, title) in enumerate(zip(items, chapters)):
        g.top = Emu(top0 + step * i)
        for s in _iter_shapes(g.shapes):
            if not s.has_text_frame:
                continue
            t = s.text_frame.text.strip()
            if re.fullmatch(r"\d{1,2}", t):
                _set_para(s.text_frame.paragraphs[0], f"{i + 1:02d}")
            elif t:
                _set_lines(s.text_frame, [title])


def _ph(slide, idx):
    return next((p for p in slide.placeholders if p.placeholder_format.idx == idx), None)


def _label(num: str, title: str) -> str:
    num = (num or "").strip()
    if not num:
        return title
    return f"{num} {title}" if any(c in num for c in ".)") else f"{num}. {title}"


def _num_dot(num: str) -> str:
    num = (num or "").strip()
    return num if not num or num.endswith((".", ")")) else num + "."


def chapter_tree(toc: list[dict]) -> list[dict]:
    """평탄한 목차 → [{item: 대제목, mids: [{item: 중제목, subs: [소제목...]}]}]"""
    chapters, cur = [], None
    for item in toc:
        lv = int(item.get("level", 1))
        if lv == 1 or cur is None:
            cur = {"item": item if lv == 1 else {"number": "", "title": "", "guide": ""}, "mids": []}
            chapters.append(cur)
            if lv == 1:
                continue
        if lv == 2 or not cur["mids"]:
            cur["mids"].append({"item": item, "subs": []})
            if lv == 2:
                continue
        cur["mids"][-1]["subs"].append(item)
    return chapters


def title_lines(title: str) -> list[str]:
    """사업명 줄 목록. 표지에 '제안서' 표시가 따로 있으므로 끝의 '제안서'는 뺀다."""
    lines = [re.sub(r"\s*제안서\s*$", "", l.strip()) for l in (title or "").splitlines() if l.strip()]
    return [l for l in lines if l] or ["사업명"]


def build_form_pptx(template: Path, toc: list[dict], title: str, out: Path, ym: str | None = None) -> Path:
    prs = Presentation(str(template))
    form = detect_form(prs)
    if form is None:
        raise ValueError("양식형 템플릿이 아닙니다.")
    src = list(prs.slides)
    lines = title_lines(title)
    ym = ym or f"{date.today():%Y.%m}"
    sec_ph, body_ph = form["section_ph"], form["body_ph"]

    chapters = chapter_tree(toc)
    ordered = []
    if "cover" in form:
        cover = src[form["cover"]]
        _fill_title_ph(cover, lines)
        _replace_date(cover, ym)
        ordered.append(cover)
    if "summary" in form:
        ordered.append(src[form["summary"]])
    if "toc" in form:
        toc_slide = src[form["toc"]]
        _fill_toc(toc_slide, [c["item"].get("title", "") for c in chapters], prs.slide_height)
        _renumber_ids(toc_slide)
        ordered.append(toc_slide)

    for ci, ch in enumerate(chapters, 1):
        sec = _duplicate(prs, src[form["section"]])
        if "num" in sec_ph and _ph(sec, sec_ph["num"]) is not None:
            _set_lines(_ph(sec, sec_ph["num"]).text_frame, [f"{ci:02d}"])
        if _ph(sec, sec_ph["title"]) is not None:
            _set_lines(_ph(sec, sec_ph["title"]).text_frame, [ch["item"].get("title", "")])
        if "list" in sec_ph and _ph(sec, sec_ph["list"]) is not None:
            mids = [m["item"].get("title", "") for m in ch["mids"]]
            if mids:
                _set_lines(_ph(sec, sec_ph["list"]).text_frame, mids)
            else:
                _clear(_ph(sec, sec_ph["list"]))
        _set_notes(sec, f"[작성 지침]\n{ch['item'].get('guide')}" if ch["item"].get("guide") else "")
        ordered.append(sec)

        leaves = [(m, s) for m in ch["mids"] for s in (m["subs"] or [None])] or [(None, None)]
        for mid, sub in leaves:
            body = _duplicate(prs, src[form["body"]])
            for sh in list(body.shapes):  # 예시 도형 제거, 개체 틀만 유지
                if not sh.is_placeholder:
                    body.shapes._spTree.remove(sh._element)
            m_item = (mid or {}).get("item") or ch["item"]
            fills = {
                "mid_num": _num_dot(m_item.get("number", "")) if mid else f"{ci}.",
                "mid_title": m_item.get("title", ""),
                "sub": _label(sub.get("number", ""), sub.get("title", "")) if sub else None,
                "head": None,
            }
            for role, idx in body_ph.items():
                ph = _ph(body, idx)
                if ph is None:
                    continue
                if fills.get(role):
                    _set_lines(ph.text_frame, [fills[role]])
                else:
                    _clear(ph)
            guide = (sub or m_item).get("guide", "")
            crumb = " > ".join(x for x in (ch["item"].get("title"), m_item.get("title") if mid else "",
                                           sub.get("title") if sub else "") if x)
            _set_notes(body, f"[위치] {crumb}" + (f"\n[작성 지침]\n{guide}" if guide else ""))
            _renumber_ids(body)
            ordered.append(body)

    if "back" in form:
        back = src[form["back"]]
        _fill_title_ph(back, lines)
        ordered.append(back)

    _replace_header_title(prs, form.get("sample_title", ""), lines)
    _finalize_order(prs, ordered)
    _replace_total_pages(prs, len(prs.slides))
    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    return out
