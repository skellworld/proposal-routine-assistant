"""업로드 문서에서 텍스트 추출 (PDF / DOCX / HWPX / HWP / TXT)."""
import re
import struct
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET

SUPPORTED = {".pdf", ".docx", ".hwpx", ".hwp", ".txt", ".md"}


class ExtractError(Exception):
    pass


def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        raise ExtractError(f"지원하지 않는 형식입니다: {ext}")
    fn = {
        ".pdf": _pdf,
        ".docx": _docx,
        ".hwpx": _hwpx,
        ".hwp": _hwp,
        ".txt": _plain,
        ".md": _plain,
    }[ext]
    try:
        text = fn(path)
    except ExtractError:
        raise
    except Exception as e:  # 손상 파일 등
        raise ExtractError(f"{path.name} 텍스트 추출 실패: {e}") from e
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _plain(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = []
    for i, page in enumerate(reader.pages, 1):
        t = page.extract_text() or ""
        if t.strip():
            pages.append(f"[p.{i}]\n{t}")
    return "\n\n".join(pages)


def _docx(path: Path) -> str:
    import docx

    d = docx.Document(str(path))
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for table in d.tables:
        for row in table.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            parts.append(" | ".join(cells))
    return "\n".join(parts)


def _hwpx(path: Path) -> str:
    out = []
    with zipfile.ZipFile(path) as z:
        sections = sorted(
            (n for n in z.namelist() if re.match(r"Contents/section\d+\.xml", n)),
            key=lambda n: int(re.findall(r"\d+", n)[-1]),
        )
        if not sections:
            raise ExtractError("HWPX 본문(section)을 찾을 수 없습니다.")
        for name in sections:
            root = ET.fromstring(z.read(name))
            for el in root.iter():
                if el.tag.endswith("}p"):
                    line = "".join(
                        (t.text or "") for t in el.iter() if t.tag.endswith("}t")
                    )
                    if line.strip():
                        out.append(line)
    # 중첩 문단(표 안 문단)이 상위 문단에도 포함되어 중복될 수 있어 연속 중복 제거
    dedup = [l for i, l in enumerate(out) if i == 0 or l != out[i - 1]]
    return "\n".join(dedup)


# ---- HWP 5.0 (OLE 바이너리) ----
_HWPTAG_PARA_TEXT = 67
_CHAR_CTRLS = {0, 10, 13, 24, 25, 26, 27, 28, 29, 30, 31}


def _hwp(path: Path) -> str:
    import olefile

    if not olefile.isOleFile(str(path)):
        raise ExtractError("올바른 HWP 파일이 아닙니다.")
    ole = olefile.OleFileIO(str(path))
    try:
        header = ole.openstream("FileHeader").read()
        flags = struct.unpack("<I", header[36:40])[0]
        if flags & 0x02:
            raise ExtractError("암호화된 HWP 파일입니다. 암호를 해제하거나 PDF로 변환해 업로드하세요.")
        if flags & 0x04:
            raise ExtractError("배포용 HWP 문서는 지원하지 않습니다. PDF로 변환해 업로드하세요.")
        compressed = bool(flags & 0x01)
        sections = sorted(
            (e for e in ole.listdir() if e[0] == "BodyText"),
            key=lambda e: int(re.findall(r"\d+", e[1])[0]),
        )
        texts = []
        for entry in sections:
            data = ole.openstream(entry).read()
            if compressed:
                data = zlib.decompress(data, -15)
            texts.append(_hwp_section_text(data))
        return "\n".join(texts)
    finally:
        ole.close()


def _hwp_section_text(data: bytes) -> str:
    out, pos, n = [], 0, len(data)
    while pos + 4 <= n:
        h = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        tag, size = h & 0x3FF, (h >> 20) & 0xFFF
        if size == 0xFFF:
            size = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        rec = data[pos : pos + size]
        pos += size
        if tag == _HWPTAG_PARA_TEXT:
            out.append(_decode_para(rec))
    return "\n".join(l for l in out if l.strip())


def _decode_para(rec: bytes) -> str:
    chars, i, n = [], 0, len(rec) // 2
    while i < n:
        c = struct.unpack_from("<H", rec, i * 2)[0]
        if c >= 32:
            chars.append(chr(c))
            i += 1
        elif c in _CHAR_CTRLS:
            if c in (10, 13):
                chars.append("\n")
            i += 1
        else:  # 인라인/확장 컨트롤: 8 wchar 차지
            if c == 9:
                chars.append("\t")
            i += 8
    return "".join(chars)
