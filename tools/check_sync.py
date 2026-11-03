"""Fails when the admin page or token store drifts from the sibling service.

LayaModel and MiniCPM52BModel keep copies of web/index.html and src/tokens.py that differ only in
the lines listed in ALLOWED. Run from either repo: py tools/check_sync.py
"""
import difflib
import re
import sys
from pathlib import Path

SHARED = ("web/index.html", "src/tokens.py", "src/settings.py")
ALLOWED = re.compile(r"^(PREFIX = |const CONFIG = )")  # per-project: token prefix and page config


def lines(path: Path) -> list[str]:
    return [("<per-project line>" if ALLOWED.match(line) else line) for line in path.read_text("utf-8").splitlines()]


def main() -> int:
    here = Path(__file__).resolve().parent.parent
    other = here.parent / ("MiniCPM52BModel" if here.name == "LayaModel" else "LayaModel")
    if not other.is_dir():
        print(f"skip: sibling {other.name} not found")
        return 0
    drift = False
    for name in SHARED:
        diff = list(difflib.unified_diff(lines(here / name), lines(other / name), here.name, other.name, lineterm="", n=0))
        if diff:
            drift = True
            print(f"{name} differs:", *diff[:40], sep="\n")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
