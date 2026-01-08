from __future__ import annotations

from pathlib import Path
from typing import List


def list_pdf_files(root: Path) -> List[Path]:
    """
    Recursively list all .pdf files under a root directory.
    """
    root = Path(root)
    if not root.exists():
        return []
    return sorted(root.rglob("*.pdf"))
