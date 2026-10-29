"""비교 분석 결과 → 표 이미지(PNG)."""
import unicodedata
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

_KR_FONTS = ["Malgun Gothic", "맑은 고딕", "NanumGothic", "AppleGothic", "Noto Sans CJK KR"]


def _set_korean_font() -> None:
    names = {f.name for f in font_manager.fontManager.ttflist}
    for f in _KR_FONTS:
        if f in names:
            plt.rcParams["font.family"] = f
            break
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
