"""Fails when the admin page or token store drifts from the sibling service.

Every model service keeps a copy of the files in SHARED that differ only in
the lines listed in ALLOWED. Run from either repo: py tools/check_sync.py
"""
import difflib
import re
import sys
from pathlib import Path

SHARED = ("web/index.html", "src/tokens.py", "src/settings.py", "src/updates.py", ".dockerignore")
OWN_COPY = {"src/updates.py": {"LayaModel"}}  # LayaModel keeps its own update flow (SERVICE-SPECIFIC.md)
ALLOWED = re.compile(r"^(PREFIX = |const CONFIG = )")  # per-project: token prefix and page config


def lines(path: Path) -> list[str]:
    return [("<per-project line>" if ALLOWED.match(line) else line) for line in path.read_text("utf-8").splitlines()]


def main() -> int:
    here = Path(__file__).resolve().parent.parent
    drift = False
    for sibling in ('LayaModel', 'MiniCPM52BModel', 'BitNet2BModel', 'TTSPiperModel', 'TTSParadeeModel', 'TEMImageModel', 'LFM3Model', 'S1MiniModel', 'SparkX26Model', 'Audio8TTSModel', 'SGLangModel'):
        other = here.parent / sibling
        if sibling == here.name or not other.is_dir():
            continue
        for name in SHARED:
            if OWN_COPY.get(name, set()) & {here.name, sibling}:
                continue
            diff = list(difflib.unified_diff(lines(here / name), lines(other / name), here.name, other.name, lineterm="", n=0))
            if diff:
                drift = True
                print(f"{name} differs from {sibling}:", *diff[:40], sep="\n")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
