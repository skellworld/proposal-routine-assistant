"""슬라이드 미리보기 이미지 (PC에 PowerPoint가 있을 때만)."""
import shutil
import subprocess
import tempfile
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent / "render.ps1"


def render(pptx: Path, out_dir: Path, width: int = 960) -> list[Path]:
    """각 슬라이드를 out_dir/sNN.png로 내보낸다. PowerPoint가 없거나 실패하면 빈 목록."""
    out_dir.mkdir(parents=True, exist_ok=True)
    # 동기화 폴더·한글 경로 문제를 피하려고 임시 폴더에 복사해서 연다
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "deck.pptx"
        shutil.copy2(pptx, src)
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(_SCRIPT),
                 "-Pptx", str(src), "-OutDir", str(out_dir), "-Width", str(width)],
                capture_output=True, timeout=300, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (subprocess.SubprocessError, OSError):
            return []
    return sorted(out_dir.glob("s*.png"))
