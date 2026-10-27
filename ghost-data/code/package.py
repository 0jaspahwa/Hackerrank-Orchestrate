"""Build code.zip at the repository root (README.md + code/, no dataset, caches, venvs or secrets).

Run:  python code/package.py
"""
from __future__ import annotations

import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INCLUDE = ["README.md", "DECISIONS.md", "docs", "code"]
SKIP_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".git", ".cache", "runs", "work"}
SKIP_SUFFIX = {".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".pyc"}
SKIP_NAMES = {".env", ".DS_Store"}


def files():
    for item in INCLUDE:
        p = REPO / item
        if p.is_file():
            yield p
            continue
        for f in sorted(p.rglob("*")):
            rel = f.relative_to(REPO)
            if f.is_file() and not (set(rel.parts) & SKIP_DIRS) and f.suffix not in SKIP_SUFFIX and f.name not in SKIP_NAMES:
                yield f


def main():
    out = REPO / "code.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files():
            z.write(f, f.relative_to(REPO).as_posix())
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
