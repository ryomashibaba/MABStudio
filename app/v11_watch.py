"""Read-only discovery of V11's explicitly published, B143-compatible handoff.

A V11 research HEAD is not a Studio release. The producer may replace
V11_READY_SOURCE.json in a synced, user-owned handoff directory; Studio only
notices it. Import still uses the frozen B143 compiler and manual approval.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from app.v11_source_import import MAX_SOURCE_JSON_BYTES, read_external_b143_source
from app.core import StudioError

READY_NAME = 'V11_READY_SOURCE.json'
# The V11 author must explicitly publish this data-only artifact. Merely moving
# a research branch HEAD cannot make new code executable inside Studio.
REMOTE_READY_NAME = 'V11_GITHUB_READY_SOURCE.json'
REMOTE_API = ('https://api.github.com/repos/ryomashibaba/MinecraftAIBuilder/'
              'contents/studio-handoff/V11_READY_SOURCE.json'
              '?ref=main')
MAX_GITHUB_RESPONSE = 1500000



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
        for filename in (READY_NAME, REMOTE_READY_NAME):
            path = directory / filename
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


def sync_v11_github_ready_source(app_root: Path, *, opener=None) -> bool:
    """Fetch only V11's explicitly published compatible SOURCE, never code.

    Public GitHub contents API is read-only. No tokens, prompts, project files,
    credentials, Minecraft connections or executable V11 modules are sent/run.
    On offline/invalid/missing handoff, keep the currently accepted Studio state.
    Returns True only when a *different* valid unapproved candidate was staged.
    """
    if opener is None:
        opener = urllib.request.urlopen
    request = urllib.request.Request(REMOTE_API, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'MABStudio/0.3-V11-Handoff',
    })
    try:
        with opener(request, timeout=7) as response:
            raw = response.read(MAX_GITHUB_RESPONSE + 1)
    except urllib.error.HTTPError as exc:
        # A missing V11 publisher is normal during integration. A withdrawn
        # candidate is no longer displayed; approved Studio history is intact.
        if exc.code == 404:
            inbox = Path(app_root) / 'v11-inbox'
            if not inbox.is_symlink():
                (inbox / REMOTE_READY_NAME).unlink(missing_ok=True)
            return False
        raise
    if len(raw) > MAX_GITHUB_RESPONSE:
        raise StudioError('V11 GitHub SOURCE配信が容量上限を超えました')
    try:
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get('type') != 'file' or \
                result.get('encoding') != 'base64' or not isinstance(result.get('content'), str) or \
                type(result.get('size')) is not int or not (0 < result['size'] <= MAX_SOURCE_JSON_BYTES):
            raise ValueError('not a bounded GitHub file')
        source_bytes = base64.b64decode(result['content'], validate=False)
        if len(source_bytes) != result['size'] or len(source_bytes) > MAX_SOURCE_JSON_BYTES:
            raise ValueError('size mismatch')
        blob_sha = hashlib.sha1(b'blob ' + str(len(source_bytes)).encode('ascii') +
                                b'\x00' + source_bytes).hexdigest()
        if result.get('sha') != blob_sha:
            raise ValueError('Git blob integrity mismatch')
    except (ValueError, UnicodeError, binascii.Error, TypeError) as exc:
        raise StudioError('V11 GitHubからのSOURCE形式・整合性が不正です') from exc

    inbox = Path(app_root) / 'v11-inbox'
    target = inbox / REMOTE_READY_NAME
    if inbox.is_symlink() or target.is_symlink():
        raise StudioError('V11 SOURCEの受け取り先がシンボリックリンクです')
    if (target.is_file() and target.stat().st_size <= MAX_SOURCE_JSON_BYTES
            and hashlib.sha256(target.read_bytes()).digest() ==
            hashlib.sha256(source_bytes).digest()):
        return False
    inbox.mkdir(parents=True, exist_ok=True)
    # Parse and check the exact downloaded payload before any visible update.
    # Unsupported shapes are rejected, not flattened or recompiled by Studio.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', prefix='.v11-stage-', suffix='.json',
                                         dir=inbox, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(source_bytes)
        try:
            source = read_external_b143_source(temporary)
        except StudioError:
            return False
        if not isinstance(source.get('elements'), list) or any(
                not isinstance(part, dict) or part.get('shape') not in
                ('box', 'ellipsoid', 'cylinder') for part in source['elements']):
            return False
        os.replace(temporary, target)
        temporary = None
        return True
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
