"""비교 분석 결과 → 표 이미지(PNG)."""
import logging
import unicodedata
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

# 나눔고딕처럼 굵은 글꼴 굵기가 700이 아닌 경우 매 글자마다 "weight bold" 경고가 찍히므로 끈다
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

_KR_FONTS = ["Malgun Gothic", "맑은 고딕", "NanumGothic", "NanumBarunGothic", "AppleGothic", "Apple SD Gothic Neo",
             "Noto Sans CJK KR", "Noto Sans KR", "UnDotum"]
_KR_FONT_HINT = "Linux 서버라면 한글 글꼴을 설치하세요: sudo apt-get install fonts-nanum"


_KR_FILE_HINTS = ("malgun", "nanum", "notosanscjk", "notosanskr", "applegothic", "applesdgothic", "undotum")


def _korean_font_name() -> str | None:
    names = {f.name for f in font_manager.fontManager.ttflist}
    return next((f for f in _KR_FONTS if f in names), None)


def _set_korean_font() -> None:
    """한글 글꼴을 찾아 지정한다. 글꼴을 나중에 설치해 matplotlib 글꼴 캐시에 없으면 시스템 글꼴 파일을 직접 등록한다."""
    found = _korean_font_name()
    if not found:
        for path in font_manager.findSystemFonts():
            if any(h in Path(path).name.lower().replace("-", "").replace("_", "") for h in _KR_FILE_HINTS):
                try:
                    font_manager.fontManager.addfont(path)
                except Exception:  # noqa: BLE001 — 읽을 수 없는 글꼴 파일은 건너뛴다
                    pass
        found = _korean_font_name()
    if found:
        plt.rcParams["font.family"] = found
    else:
        print(f"[경고] 한글 글꼴을 찾지 못해 비교표 이미지의 한글이 깨질 수 있습니다. {_KR_FONT_HINT}", flush=True)
    plt.rcParams["axes.unicode_minus"] = False


CHANGE_COLORS = {
    "신규": "#DCFCE7",
    "삭제": "#FEE2E2",
    "변경": "#FEF3C7",
    "동일": "#F3F4F6",
    "확인필요": "#E0E7FF",
}

# (헤더, 키, 폭 비율, 줄당 표시폭)
COLUMNS = [
    ("항목", "item", 0.12, 24),
    ("과거 (RFP·공고)", "past", 0.27, 56),
    ("현재 (RFP·공고)", "current", 0.27, 56),
    ("변경", "change", 0.07, 12),
    ("시사점", "insight", 0.27, 56),
]


def _w(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _wrap(text: str, width: int) -> list[str]:
    lines = []
    for para in str(text or "").splitlines() or [""]:
        cur, cw = "", 0
        for ch in para:
            if cw + _w(ch) > width:
                lines.append(cur)
                cur, cw = "", 0
                if ch == " ":
                    continue
            cur += ch
            cw += _w(ch)
        lines.append(cur)
    return lines or [""]


def render_compare_table(result: dict, out: Path, title: str = "") -> Path:
    _set_korean_font()
    rows = result.get("rows", [])
    line_h = 0.24  # inch
    pad = 0.12
    fig_w = 16
    def cell(r: dict, key: str) -> str:
        if key == "item" and r.get("source") in ("공고서", "RFP"):
            return f"{r.get('item', '')}\n({r['source']})"
        return r.get(key, "")

    wrapped = [[_wrap(cell(r, k), cpl) for _, k, _, cpl in COLUMNS] for r in rows]
    heights = [max(len(c) for c in row) * line_h + pad * 2 for row in wrapped]
    header_h = 0.5
    title_h = 0.8 if title else 0.2
    fig_h = title_h + header_h + sum(heights) + 0.3

    fig = plt.figure(figsize=(fig_w, fig_h), dpi=150)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, fig_w)
    ax.set_ylim(fig_h, 0)
    ax.axis("off")

    margin = 0.3
    table_w = fig_w - margin * 2
    xs, x = [], margin
    for _, _, ratio, _ in COLUMNS:
        xs.append((x, ratio * table_w))
        x += ratio * table_w

    if title:
        ax.text(margin, 0.45, title, fontsize=16, fontweight="bold", va="center")

    y = title_h
    for (hx, hw), (name, *_rest) in zip(xs, COLUMNS):
        ax.add_patch(Rectangle((hx, y), hw, header_h, facecolor="#1E3A8A", edgecolor="white", lw=1))
        ax.text(hx + hw / 2, y + header_h / 2, name, color="white", fontsize=11,
                fontweight="bold", ha="center", va="center")
    y += header_h

    for row, cells, h in zip(rows, wrapped, heights):
        change = row.get("change", "")
        for i, ((cx, cw), lines) in enumerate(zip(xs, cells)):
            key = COLUMNS[i][1]
            if key == "change":
                fc = CHANGE_COLORS.get(change, "white")
            elif key == "item":
                fc = "#F8FAFC"
            else:
                fc = "white"
            ax.add_patch(Rectangle((cx, y), cw, h, facecolor=fc, edgecolor="#CBD5E1", lw=0.8))
            centered = key in ("change",)
            ax.text(
                cx + (cw / 2 if centered else 0.1),
                y + (h / 2 if centered else pad),
                "\n".join(lines),
                fontsize=9.5,
                fontweight="bold" if key in ("item", "change") else "normal",
                ha="center" if centered else "left",
                va="center" if centered else "top",
                linespacing=1.45,
            )
        y += h

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, facecolor="white")
    plt.close(fig)
    return out
