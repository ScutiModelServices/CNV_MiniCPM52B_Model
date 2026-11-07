"""Publish the deployment-only files of a source ref to the `live` branch.

Builds a commit from the paths in DEPLOY_PATHS using a temporary git index, so
your working tree, index and current branch are never touched. The commit's
parent is the remote `live` tip, so the push is a normal fast-forward.

A plain publish only updates `live` (use it for tests). --release also tags the
source commit vX.Y.Z and the live commit live-vX.Y.Z, X.Y.Z being the VERSION file.
A version already released with different live files is refused: bump VERSION, or
add --replace to move that version's tags while it is not officially released yet.

    python tools/publish_live.py              # publish main
    python tools/publish_live.py --release    # publish and tag a release
    python tools/publish_live.py --dry-run    # show what would be published
    python tools/publish_live.py --source HEAD
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Everything the image builds from and the compose/nginx files that run it. Add a path here when a service is added.
DEPLOY_PATHS = [
    "Dockerfile",
    "docker-compose.yml",
    ".env.example",
    "VERSION",
    "nginx.conf",
    "pyproject.toml",
    "uv.lock",  # the Dockerfile copies it: `uv sync --frozen`
    "src",
    "web",
    "README.md",
    "deployment.md",  # README links to it
    "CHANGELOG.md",  # README links to it
    "docs",  # README links to it
]
# Left out of live: Python tests/ folders are not built into the image.
EXCLUDE_DIRS = {"tests"}
EXCLUDE_PREFIXES: tuple[str, ...] = ()
BRANCH = "live"
REMOTE = "origin"
ROOT = Path(__file__).resolve().parent.parent


def git(*args: str, env: dict | None = None, stdin: str | None = None) -> str:
    # Bytes in and out: text mode would turn "\n" into "\r\n" on Windows and corrupt the index entries.
    result = subprocess.run(
        ["git", *args], cwd=ROOT, env=env, input=stdin.encode() if stdin else None, capture_output=True
    )
    if result.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed:\n{result.stderr.decode(errors='replace').strip()}")
    return result.stdout.decode(errors="replace").strip()


def remote_live_tip() -> str | None:
    """The remote `live` commit, fetched so its tree can be compared; None if the branch does not exist yet."""
    line = git("ls-remote", "--heads", REMOTE, f"refs/heads/{BRANCH}")
    if not line:
        return None
    tip = line.split()[0]
    git("fetch", "--quiet", REMOTE, f"refs/heads/{BRANCH}")
    return tip


def build_tree(source: str) -> str:
    """Write a tree holding only DEPLOY_PATHS from `source`, using a throwaway index."""
    kept = [
        line
        for line in git("ls-tree", "-r", source, "--", *DEPLOY_PATHS).splitlines()
        if not (
            line.split("\t", 1)[1].startswith(EXCLUDE_PREFIXES)
            or EXCLUDE_DIRS.intersection(line.split("\t", 1)[1].split("/")[:-1])
        )
    ]
    listing = "\n".join(kept)
    if not listing:
        sys.exit(f"None of the deploy paths exist in {source}.")
    present = {line.split("\t", 1)[1] for line in listing.splitlines()}
    for path in DEPLOY_PATHS:
        if not any(p == path or p.startswith(path + "/") for p in present):
            print(f"warning: {path} not found in {source}", file=sys.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git("update-index", "--add", "--index-info", env=env, stdin=listing + "\n")
        return git("write-tree", env=env)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default="main", help="ref to publish from (default: main)")
    parser.add_argument("--release", action="store_true", help="also tag the source vX.Y.Z and the live commit live-vX.Y.Z (X.Y.Z from VERSION)")
    parser.add_argument("--replace", action="store_true", help="with --release: move the tags of an already tagged version (force-pushes the two tags)")
    parser.add_argument("--dry-run", action="store_true", help="build and report, push nothing")
    args = parser.parse_args()

    source_sha = git("rev-parse", "--verify", f"{args.source}^{{commit}}")
    tree = build_tree(source_sha)
    count = len(git("ls-tree", "-r", "--name-only", tree).splitlines())
    parent = remote_live_tip()
    changed = tree != (git("rev-parse", f"{parent}^{{tree}}") if parent else None)
    print(f"source {args.source} ({source_sha[:8]}) -> {count} files, tree {tree[:8]}")

    source_tag = live_tag = None
    if args.release:
        version = git("show", f"{source_sha}:VERSION").strip()
        source_tag, live_tag = f"v{version}", f"live-v{version}"
        released = git("ls-remote", "--tags", REMOTE, f"refs/tags/{live_tag}^{{}}").split()
        if released and not args.replace and (changed or released[0] != parent):
            sys.exit(f"{live_tag} is already released with different files. Bump VERSION, commit, then release again.")
        if released and not args.replace:
            print(f"{live_tag} is already released.")
            return
    if not changed and not args.release:
        print(f"{BRANCH} is already up to date.")
        return

    subject = git("log", "-1", "--format=%s", source_sha)
    message = f"live: {source_sha[:8]} {subject}"
    if args.dry_run:
        if changed:
            print(f"dry run: would commit '{message}' on top of {parent[:8] if parent else 'nothing (new branch)'}")
        if live_tag:
            print(f"dry run: would tag {source_sha[:8]} as {source_tag} and the live commit as {live_tag}")
        return

    commit = git("commit-tree", tree, *(["-p", parent] if parent else []), "-m", message) if changed else parent
    refs = [f"{commit}:refs/heads/{BRANCH}"] if changed else []
    if live_tag:
        # -f and the + below move existing tags; the check above only lets that through with --replace.
        git("tag", "-f", "-a", source_tag, source_sha, "-m", source_tag)
        git("tag", "-f", "-a", live_tag, commit, "-m", live_tag)
        refs += [f"+refs/tags/{source_tag}", f"+refs/tags/{live_tag}"]
    git("push", REMOTE, *refs)
    print(f"pushed {commit[:8]} to {REMOTE}/{BRANCH}" + (f", tagged {source_tag} and {live_tag}" if live_tag else ""))


if __name__ == "__main__":
    main()
