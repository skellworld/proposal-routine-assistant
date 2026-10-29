"""템플릿 + 목차 → 제안서 뼈대 PPTX."""
import copy
from datetime import date
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, PP_PLACEHOLDER
from pptx.util import Emu, Inches, Pt

DEFAULT_TEMPLATE_NAME = "기본템플릿.pptx"

NAVY = RGBColor(0x1E, 0x3A, 0x8A)
SKY = RGBColor(0x38, 0xBD, 0xF8)


# ---------------- 기본 템플릿 ----------------
def ensure_default_template(templates_dir: Path) -> None:
    templates_dir.mkdir(parents=True, exist_ok=True)
    path = templates_dir / DEFAULT_TEMPLATE_NAME
    if not path.exists():
        create_default_template(path)


def create_default_template(path: Path) -> None:
    """python-pptx 기본 레이아웃을 16:9로 확장하고 상단 띠/하단 선을 마스터에 넣은 기본 템플릿."""
    prs = Presentation()
    k = 13.333 / 10
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    holders = list(prs.slide_master.placeholders)
    for layout in prs.slide_layouts:
        holders += list(layout.placeholders)
    for ph in holders:
        if ph.left is not None and ph.width is not None:
            ph.left, ph.width = Emu(int(ph.left * k)), Emu(int(ph.width * k))

    # 마스터 장식: 임시 슬라이드에서 도형을 만든 뒤 마스터 spTree로 옮긴다.
    tmp = prs.slides.add_slide(prs.slide_layouts[6])
    band = tmp.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, Inches(0.18))
    line = tmp.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.5), Inches(7.05), prs.slide_width - Inches(1.0), Pt(1.5))
    for shp, color in ((band, NAVY), (line, SKY)):
        shp.fill.solid()
        shp.fill.fore_color.rgb = color
        shp.line.fill.background()
    master_tree = prs.slide_master.shapes._spTree
    for shp in (band, line):
        master_tree.insert(2, copy.deepcopy(shp._element))
    _remove_all_slides(prs)

    # 제목 서체/색 지정
    for layout in prs.slide_layouts:
        for ph in layout.placeholders:
            if ph.placeholder_format.type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE):
                for p in ph.text_frame.paragraphs:
                    p.font.color.rgb = NAVY
                    p.font.bold = True
    path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(path))


# ---------------- 레이아웃 탐색 ----------------
def _ph_types(layout) -> set:
    return {ph.placeholder_format.type for ph in layout.placeholders}


def _find_layout(prs, names: tuple[str, ...], need: set, avoid: set = frozenset()):
    layouts = list(prs.slide_layouts)
    for lo in layouts:
        n = lo.name.lower()
        if any(k in n for k in names):
            return lo
    for lo in layouts:
        t = _ph_types(lo)
        if need <= t and not (avoid & t):
            return lo
    return layouts[0]


def _layouts(prs, mapping: dict | None = None) -> dict:
    found = _auto_layouts(prs)
    by_name = {lo.name: lo for lo in prs.slide_layouts}
    for role, name in (mapping or {}).items():
        if name in by_name and role in found:
            found[role] = by_name[name]
    return found


def layout_info(path: Path) -> dict:
    """템플릿 관리 화면용: 슬라이드 크기, 레이아웃 목록, 자동 매핑 결과."""
    prs = Presentation(str(path))
    ratio = prs.slide_width / prs.slide_height
    auto = _auto_layouts(prs)
    return {
        "slide_size": "16:9" if abs(ratio - 16 / 9) < 0.05 else "4:3" if abs(ratio - 4 / 3) < 0.05 else f"{ratio:.2f}:1",
        "layouts": [lo.name for lo in prs.slide_layouts],
        "auto_mapping": {role: lo.name for role, lo in auto.items()},
        "slide_count": len(prs.slides),
    }


def _auto_layouts(prs) -> dict:
    title = _find_layout(prs, ("title slide", "제목 슬라이드", "표지", "cover"), {PP_PLACEHOLDER.CENTER_TITLE})
    section = _find_layout(prs, ("section", "구역", "섹션", "간지"), {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.BODY},
                           {PP_PLACEHOLDER.OBJECT})
    content = _find_layout(prs, ("title and content", "제목 및 내용", "본문", "content"),
                           {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.OBJECT})
    return {"title": title, "section": section, "content": content}


# ---------------- 슬라이드 작성 ----------------
def _remove_all_slides(prs) -> None:
    sld_ids = prs.slides._sldIdLst
    for sld in list(sld_ids):
        prs.part.drop_rel(sld.rId)
        sld_ids.remove(sld)


def _title_ph(slide):
    for ph in slide.placeholders:
        if ph.placeholder_format.type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE):
            return ph
    return None


def _body_ph(slide):
    for ph in slide.placeholders:
        if ph.placeholder_format.type in (PP_PLACEHOLDER.SUBTITLE, PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT):
            return ph
    return None


def _add(prs, layout, title: str, body_lines: list[str] | None = None, notes: str = ""):
    slide = prs.slides.add_slide(layout)
    tp = _title_ph(slide)
    if tp is not None:
        tp.text = title
    else:
        box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), prs.slide_width - Inches(1), Inches(1))
        box.text_frame.text = title
        box.text_frame.paragraphs[0].font.size = Pt(28)
        box.text_frame.paragraphs[0].font.bold = True
    if body_lines:
        bp = _body_ph(slide)
        if bp is not None:
            tf = bp.text_frame
            tf.text = body_lines[0]
            for line in body_lines[1:]:
                tf.add_paragraph().text = line
    if notes:
        slide.notes_slide.notes_text_frame.text = notes
    return slide


def _label(item: dict) -> str:
    num = (item.get("number") or "").strip()
    title = (item.get("title") or "").strip()
    if not num:
        return title
    sep = " " if any(c in num for c in ".)") else ". "
    return f"{num}{sep}{title}"


SAMPLE_TOC = [
    {"level": 1, "number": "Ⅰ", "title": "제안 개요", "guide": "제안 목적·범위·특장점"},
    {"level": 2, "number": "1", "title": "제안 목적 및 범위", "guide": ""},
    {"level": 2, "number": "2", "title": "제안의 특장점", "guide": ""},
    {"level": 1, "number": "Ⅱ", "title": "기술 부문", "guide": ""},
    {"level": 2, "number": "1", "title": "수행 방법론", "guide": ""},
    {"level": 3, "number": "1.1", "title": "세부 수행 절차", "guide": ""},
    {"level": 1, "number": "Ⅲ", "title": "사업 관리", "guide": ""},
]


def build_proposal_pptx(template: Path, toc: list[dict], proposal_title: str, company: str, out: Path,
                        mapping: dict | None = None, ym: str | None = None) -> Path:
    from .pptx_form import build_form_pptx, detect_form
    from .pptx_token import build_token_pptx, detect_token_form

    probe = Presentation(str(template))
    if detect_token_form(probe) is not None:  # 토큰형 양식({{토큰}} + #역할 노트)
        return build_token_pptx(template, toc, proposal_title, out, ym)
    if detect_form(probe) is not None:  # 안내 문구형 양식
        return build_form_pptx(template, toc, proposal_title, out, ym)
    proposal_title = " ".join(proposal_title.splitlines())
    prs = Presentation(str(template))
    _remove_all_slides(prs)
    lo = _layouts(prs, mapping)

    _add(prs, lo["title"], proposal_title or "제안서",
         [f"{company}  |  {date.today():%Y. %m.}" if company else f"{date.today():%Y. %m.}"])

    chapters = [t for t in toc if t.get("level", 1) == 1]
    toc_lines = [_label(c) for c in chapters] or [_label(t) for t in toc]
    # 목차가 길면 12개씩 나눠서
    for i in range(0, max(len(toc_lines), 1), 12):
        _add(prs, lo["content"], "목차" if i == 0 else "목차 (계속)", toc_lines[i:i + 12])

    path: list[str] = ["", "", ""]
    for idx, item in enumerate(toc):
        level = int(item.get("level", 1))
        label = _label(item)
        path[level - 1] = label
        for j in range(level, 3):
            path[j] = ""
        guide = (item.get("guide") or "").strip()
        notes = f"[작성 지침]\n{guide}" if guide else ""
        if level == 1:
            subs = []
            for nxt in toc[idx + 1:]:
                if int(nxt.get("level", 1)) == 1:
                    break
                if int(nxt.get("level", 1)) == 2:
                    subs.append(_label(nxt))
            _add(prs, lo["section"], label, subs or None, notes)
            if not subs:  # 하위 목차가 없으면 본문 슬라이드 1장
                _add(prs, lo["content"], label, None, notes)
        else:
            has_child = idx + 1 < len(toc) and int(toc[idx + 1].get("level", 1)) > level
            if has_child:  # 하위가 있는 중목차는 소목차 슬라이드에서 다룸
                continue
            crumb = " > ".join(p for p in path[: level - 1] if p)
            _add(prs, lo["content"], label, None, (f"[위치] {crumb}\n" if crumb else "") + notes)

    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    return out
