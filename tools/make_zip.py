#!/usr/bin/env python3
"""Zip up this repo, excluding everything covered by .gitignore.

Uses `git ls-files` to determine which files to include, so all
.gitignore rules (including nested ones and negation patterns) are
honored automatically. Output goes to _zip_versions/, which is itself
gitignored, as <name>-<VERSION>.zip. tools/ (maintainer scripts) and
older zips are left out; .env.example is tracked, so it is included.
"""

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "_zip_versions"


def get_included_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return [
        line
        for line in result.stdout.splitlines()
        if line and not line.startswith("tools/") and not line.endswith(".zip")
    ]


def main() -> None:
    files = get_included_files()
    if not files:
        print("No files found to zip.", file=sys.stderr)
        sys.exit(1)

    OUTPUT_DIR.mkdir(exist_ok=True)
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    zip_path = OUTPUT_DIR / f"{ROOT.name}-{version}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_path in files:
            full_path = ROOT / rel_path
            if full_path.is_file():
                zf.write(full_path, rel_path)

    print(f"Created {zip_path} ({len(files)} files)")


if __name__ == "__main__":
    main()
