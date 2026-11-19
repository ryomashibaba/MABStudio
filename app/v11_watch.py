"""Read-only discovery of V11's explicitly published, B143-compatible handoff.

A V11 research HEAD is not a Studio release. The producer may replace
V11_READY_SOURCE.json in a synced, user-owned handoff directory; Studio only
notices it. Import still uses the frozen B143 compiler and manual approval.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from app.v11_source_import import MAX_SOURCE_JSON_BYTES, read_external_b143_source
from app.core import StudioError

READY_NAME = 'V11_READY_SOURCE.json'


def candidate_directories(app_root: Path) -> list[Path]:
    root = Path(app_root)
    directories = [root / 'v11-inbox']
    trusted_feed = root / 'auto-update' / 'trusted-feed.txt'
    if trusted_feed.is_file():
        try:
            candidate = Path(trusted_feed.read_text(encoding='utf-8-sig').strip())
            if candidate.is_absolute() and candidate.is_dir():
                directories.append(candidate)
        except (OSError, ValueError):
            pass
    return directories


def newest_v11_ready_source(app_root: Path, seen_sha: str = '') -> tuple[Path, str, str] | None:
    """Return (path, content SHA256, title) or None; never mutate a project.

    Invalid or incompatible drafts are ignored, not auto-converted or executed.
    The pinned engine performs the authoritative validation after selection.
    """
    candidates = []
    for directory in candidate_directories(app_root):
        path = directory / READY_NAME
        try:
            if path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
            if 0 < stat.st_size <= MAX_SOURCE_JSON_BYTES:
                candidates.append((stat.st_mtime_ns, path))
        except OSError:
            continue
    for _, path in sorted(candidates, reverse=True):
        try:
            data = path.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            if sha == seen_sha:
                continue
            source = read_external_b143_source(path)
            # Keep the watch button for known fixed-B143 primitives only.
            # The actual compiler still validates all geometry on import.
            if any(part.get('shape') not in ('box', 'ellipsoid', 'cylinder')
                   for part in source['elements']):
                continue
            return path, sha, source['title'][:80]
        except (StudioError, OSError, ValueError, KeyError, TypeError):
            continue
    return None
