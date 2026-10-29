"""템플릿(.pptx) 자동 분석 → 설명 문구 생성.

PPTX 구조(크기·테마 색상·글꼴·레이아웃·예시 슬라이드 텍스트)를 추출하고,
API 키가 있으면 Claude로 자연스러운 설명을 만들며, 없거나 실패하면 규칙 기반 설명을 쓴다.
"""
import colorsys
import json
import tempfile
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.opc.constants import RELATIONSHIP_TYPE as RT

from . import thumbs
from .claude_client import ClaudeError, available, call_json, image_block

_NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
_COLOR_SLOTS = ("dk2", "lt2", "accent1", "accent2", "accent3", "accent4")


def _theme(prs) -> tuple[dict, dict]:
    """테마 색상(slot → hex)과 글꼴(major/minor)."""
    colors, fonts = {}, {}
    try:
        theme = prs.slide_master.part.part_related_by(RT.THEME)
        root = etree.fromstring(theme.blob)
    except (KeyError, etree.XMLSyntaxError):
        return colors, fonts
    for slot in _COLOR_SLOTS:
        el = root.find(f".//a:clrScheme/a:{slot}", _NS)
        if el is None or not len(el):
            continue
        c = el[0]
        val = c.get("val") if c.tag.endswith("srgbClr") else c.get("lastClr")
        if val:
            colors[slot] = "#" + val.upper()
    for kind in ("major", "minor"):
        f = root.find(f".//a:fontScheme/a:{kind}Font", _NS)
        if f is None:
            continue
        ea = f.find("a:ea", _NS)
        latin = f.find("a:latin", _NS)
        hangul = f.find("a:font[@script='Hang']", _NS)
        name = next((x.get("typeface") for x in (hangul, ea, latin) if x is not None and x.get("typeface")), "")
        if name:
            fonts[kind] = name
    return colors, fonts


def color_name(hex_color: str) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if s < 0.12:
        return "흰색" if l > 0.9 else "검정" if l < 0.15 else "회색"
    deg = h * 360
    names = [(15, "빨강"), (40, "주황"), (65, "노랑"), (160, "초록"), (200, "청록"), (250, "파랑"), (290, "남보라"),
             (335, "보라"), (360, "빨강")]
    name = next(n for d, n in names if deg <= d)
    if name == "파랑" and l < 0.35:
        name = "남색"
    return ("진한 " if l < 0.3 and name != "남색" else "연한 " if l > 0.75 else "") + name


def extract_features(path: Path) -> dict:
    prs = Presentation(str(path))
    ratio = prs.slide_width / prs.slide_height
    colors, fonts = _theme(prs)
    texts = []
    for s in list(prs.slides)[:15]:
        parts = [sh.text_frame.text.strip().replace("\n", " ") for sh in s.shapes
                 if sh.has_text_frame and sh.text_frame.text.strip()]
        if parts:
            texts.append(" / ".join(parts)[:160])
    return {
        "file": path.name,
        "slide_size": "16:9" if abs(ratio - 16 / 9) < 0.05 else "4:3" if abs(ratio - 4 / 3) < 0.05 else f"{ratio:.2f}:1",
        "theme_colors": colors,
        "fonts": fonts,
        "layouts": [lo.name for lo in prs.slide_layouts],
        "slide_count": len(prs.slides),
        "sample_texts": texts,
    }


def rule_description(f: dict) -> str:
    accents = [c for k, c in f["theme_colors"].items() if k.startswith("accent")][:2]
    palette = " · ".join(dict.fromkeys(color_name(c) for c in ([f["theme_colors"].get("dk2")] if f["theme_colors"].get("dk2") else []) + accents))
    parts = [f"{f['slide_size']} 비율"]
    if palette:
        parts.append(f"{palette} 계열 색상")
    if f["fonts"]:
        parts.append("글꼴 " + " / ".join(dict.fromkeys(f["fonts"].values())))
    parts.append(f"레이아웃 {len(f['layouts'])}종")
    desc = ", ".join(parts) + " 템플릿."
    if f["sample_texts"]:
        first = f["sample_texts"][0].split(" / ")[0]
        first = first if len(first) <= 30 else first[:30] + "…"
        desc += f" 예시 슬라이드 {f['slide_count']}장 포함(예: '{first}')."
    return desc


_SCHEMA = {
    "type": "object",
    "properties": {"description": {"type": "string"}},
    "required": ["description"],
    "additionalProperties": False,
}

_SYSTEM = """당신은 제안서 PPT 템플릿을 정리하는 디자인 담당자입니다.
템플릿 구조 정보(비율, 테마 색상, 글꼴, 레이아웃, 예시 슬라이드 텍스트)를 보고
템플릿 목록에서 고를 때 도움이 되는 한국어 설명을 1~2문장(120자 이내)으로 씁니다.
색상은 hex 대신 '남색·하늘색'처럼 말로 표현하고, 어떤 제안(예: 공공 SI, 컨설팅, 보안)에 어울리는지 추정할 근거가 있으면 덧붙입니다.
정보에 없는 내용은 지어내지 않습니다."""


def describe(path: Path) -> tuple[str, str]:
    """(설명, 방식) — 방식은 'claude' 또는 'rule'."""
    f = extract_features(path)
    if available():
        try:
            content = []
            with tempfile.TemporaryDirectory() as tmp:
                # 실제 색감은 테마 색상표보다 슬라이드 그림이 정확하므로 썸네일을 함께 보여준다
                pngs = thumbs.render(path, Path(tmp) / "t", width=480)
                from .template_struct import contact_sheets
                sheets = contact_sheets(pngs[:12], Path(tmp), per_sheet=12, cols=4, cell_w=300)
                content = [image_block(p, "슬라이드 썸네일") for p in sheets[:1]]
                note = "썸네일이 있으면 색·분위기는 썸네일 기준으로 쓰고, 테마 색상표는 참고만 하세요.\n" if content else ""
                content.append({"type": "text", "text": note + json.dumps(f, ensure_ascii=False, indent=1)})
                out = call_json(_SYSTEM, content, _SCHEMA, max_tokens=4000, light=True)
            if out.get("description", "").strip():
                return out["description"].strip(), "claude"
        except ClaudeError:
            pass
    return rule_description(f), "rule"
