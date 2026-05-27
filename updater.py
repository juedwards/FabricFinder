"""Self-update: pull newer commits from the git remote and restart.

Called once at startup (see bot.py). If the working tree is a git checkout and
the upstream branch has newer commits, we fast-forward and re-exec the current
Python process so the new code takes effect.

Safety:
- No-op if `.git` is missing, `git` is not on PATH, or there is no upstream.
- No-op if the working tree has local modifications (we don't want to clobber
  uncommitted work).
- Guarded by the FABRICFINDER_UPDATED env var so we never loop forever.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
_GUARD_ENV = "FABRICFINDER_UPDATED"


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_DIR,
        check=check,
        capture_output=True,
        text=True,
    )


def _log(msg: str) -> None:
    print(f"[updater] {msg}", flush=True)


def check_and_update() -> None:
    # Avoid infinite restart loop.
    if os.environ.get(_GUARD_ENV) == "1":
        return

    if not os.path.isdir(os.path.join(REPO_DIR, ".git")):
        return
    if shutil.which("git") is None:
        return

    try:
        # Need an upstream to compare against.
        up = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
                  check=False)
        if up.returncode != 0 or not up.stdout.strip():
            return

        # Refuse to touch a dirty tree.
        dirty = _git("status", "--porcelain", check=False)
        if dirty.returncode == 0 and dirty.stdout.strip():
            _log("local changes detected; skipping auto-update.")
            return

        _log("checking for updates...")
        fetch = _git("fetch", "--quiet", check=False)
        if fetch.returncode != 0:
            _log(f"git fetch failed: {fetch.stderr.strip() or fetch.stdout.strip()}")
            return

        local = _git("rev-parse", "HEAD", check=False).stdout.strip()
        remote = _git("rev-parse", "@{u}", check=False).stdout.strip()
        if not local or not remote or local == remote:
            return

        # Only update when upstream is strictly ahead of local.
        behind = _git("rev-list", "--count", "HEAD..@{u}", check=False)
        if behind.returncode != 0 or behind.stdout.strip() in {"", "0"}:
            return

        _log(f"new version available ({local[:7]} -> {remote[:7]}); pulling...")
        pull = _git("pull", "--ff-only", "--quiet", check=False)
        if pull.returncode != 0:
            _log(f"git pull failed: {pull.stderr.strip() or pull.stdout.strip()}")
            return

        _log("update applied; restarting...")
    except FileNotFoundError:
        return
    except Exception as e:  # pragma: no cover - defensive
        _log(f"unexpected error: {e}")
        return

    # Re-exec the current Python process with the same args.
    env = os.environ.copy()
    env[_GUARD_ENV] = "1"
    try:
        os.execvpe(sys.executable, [sys.executable, *sys.argv], env)
    except Exception as e:  # pragma: no cover
        _log(f"restart failed: {e}; continuing with current version.")
