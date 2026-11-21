"""Safe, background, opt-in-free updates of the *portable app*, never of B143.

A verified GitHub Release is downloaded into a separate user-owned version
folder while the GUI remains usable. Switching occurs only at NEXT launch, so
drafts, local model requests, SocketBE sessions and worlds are never interrupted.
No privileged installer, project migration or write to an existing EXE.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile

from app.core import atomic_json, user_root

REPO = 'ryomashibaba/MABStudio'
API = 'https://api.github.com/repos/' + REPO + '/releases/latest'
ASSET_NAME = 'MABStudio-Windows-Native-Preview.zip'
TAG_RE = re.compile(r'^mabstudio-v03-([a-f0-9]{40})$')
SHA_RE = re.compile(r'^[a-f0-9]{64}$')
MAX_DOWNLOAD = 500 * 1024 * 1024
MAX_UNPACKED = 1300 * 1024 * 1024
MAX_FILES = 12000
READY_SCHEMA = 'mab-studio-portable-ready-v1'
_ROOT = 'portable-versions'


def _base() -> Path:
    return user_root() / 'auto-update'


def _state_file() -> Path:
    return _base() / 'portable-ready.json'


def _manifest(exe: Path) -> dict:
    try:
        path = exe.parent / 'RUNTIME_MANIFEST.json'
        if path.stat().st_size > 600_000:
            return {}
        obj = json.loads(path.read_text(encoding='utf-8'))
        if obj.get('schema') != 'mab-studio-critical-runtime-manifest-v1':
            return {}
        return obj
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _read_json(path: Path) -> dict:
    try:
        if path.stat().st_size > 16384:
            return {}
        obj = json.loads(path.read_text(encoding='utf-8'))
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def _download_metadata(*, opener=None) -> dict:
    """Only GitHub's latest non-draft/non-prerelease SHA-tagged release."""
    open_url = opener or urllib.request.urlopen
    req = urllib.request.Request(API, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'MABStudio/0.3-portable-update',
    })
    with open_url(req, timeout=5) as response:
        raw = response.read(128 * 1024 + 1)
    if len(raw) > 128 * 1024:
        raise ValueError('UPDATE_METADATA_TOO_LARGE')
    release = json.loads(raw)
    tag = release.get('tag_name', '')
    match = TAG_RE.fullmatch(tag) if isinstance(tag, str) else None
    if not match or release.get('draft') is not False or release.get('prerelease') is not False:
        raise ValueError('UPDATE_NOT_APPROVED_RELEASE')
    sha = match.group(1)
    if release.get('target_commitish') != sha:
        raise ValueError('UPDATE_RELEASE_SHA_MISMATCH')
    assets = release.get('assets')
    if not isinstance(assets, list):
        raise ValueError('UPDATE_ASSETS_MISSING')
    matches = [a for a in assets if isinstance(a, dict) and a.get('name') == ASSET_NAME]
    if len(matches) != 1:
        raise ValueError('UPDATE_ASSET_MISSING_OR_DUPLICATE')
    asset = matches[0]
    size = asset.get('size')
    digest = asset.get('digest')
    url = asset.get('browser_download_url')
    wanted = 'https://github.com/' + REPO + '/releases/download/' + tag + '/' + ASSET_NAME
    if (type(size) is not int or size < 1_000_000 or size > MAX_DOWNLOAD or
        not isinstance(digest, str) or not digest.startswith('sha256:') or
        not SHA_RE.fullmatch(digest[7:]) or url != wanted):
        raise ValueError('UPDATE_ASSET_NOT_VERIFIABLE')
    date = release.get('published_at')
    if not isinstance(date, str) or not date.endswith('Z'):
        raise ValueError('UPDATE_DATE_INVALID')
    datetime.fromisoformat(date.replace('Z', '+00:00'))
    return {'sha': sha, 'digest': digest[7:], 'url': url,
            'size': size, 'publishedAt': date}


def _validate_zip(archive: Path) -> None:
    """Reject traversal, links, duplicate and zip-bomb entries before extraction."""
    total = 0
    seen = set()
    with zipfile.ZipFile(archive, 'r') as z:
        members = z.infolist()
        if not (10 <= len(members) <= MAX_FILES):
            raise ValueError('UPDATE_ZIP_ENTRY_COUNT')
        for member in members:
            raw = member.orig_filename
            name = member.filename
            if raw != name or '\\' in raw or ':' in raw or raw.startswith('/'):
                raise ValueError('UPDATE_ZIP_UNSAFE_NAME')
            p = PurePosixPath(name)
            if (p.is_absolute() or not p.parts or p.parts[0] != 'MABStudio'
                or any(part in ('', '.', '..') for part in name.split('/') if part != '')
                or name in seen):
                raise ValueError('UPDATE_ZIP_UNSAFE_PATH')
            seen.add(name)
            if (member.external_attr >> 16) & 0o170000 == stat.S_IFLNK:
                raise ValueError('UPDATE_ZIP_SYMLINK')
            total += member.file_size
            if total > MAX_UNPACKED or member.file_size > 175 * 1024 * 1024:
                raise ValueError('UPDATE_ZIP_BOMB')
        required = {'MABStudio/MABStudio.exe', 'MABStudio/node.exe',
                    'MABStudio/RUNTIME_MANIFEST.json',
                    'MABStudio/_internal/app/education_runtime/build.mjs'}
        if not required.issubset(seen):
            raise ValueError('UPDATE_ZIP_REQUIRED_FILES_MISSING')


def _fetch_zip(release: dict, archive: Path, *, opener=None) -> None:
    open_url = opener or urllib.request.urlopen
    request = urllib.request.Request(release['url'], headers={
        'User-Agent': 'MABStudio/0.3-portable-update',
    })
    total = 0
    hash_state = hashlib.sha256()
    with open_url(request, timeout=30) as response, archive.open('wb') as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_DOWNLOAD or total > release['size']:
                raise ValueError('UPDATE_DOWNLOAD_TOO_LARGE')
            hash_state.update(chunk)
            output.write(chunk)
    if total != release['size'] or hash_state.hexdigest() != release['digest']:
        raise ValueError('UPDATE_DOWNLOAD_SHA256_MISMATCH')


def _version_dir(sha: str) -> Path:
    if not re.fullmatch(r'[a-f0-9]{40}', sha):
        raise ValueError('UPDATE_SHA_INVALID')
    return _base() / _ROOT / sha


def prepare_update() -> bool:
    """Download and stage, with no GUI restart and no world/model connections."""
    if not getattr(sys, 'frozen', False) or os.environ.get('MAB_STUDIO_DISABLE_AUTO_UPDATE') == '1':
        return False
    this = _manifest(Path(sys.executable))
    current = this.get('studioGitSha', '')
    if not re.fullmatch(r'[a-f0-9]{40}', current):
        return False
    release = _download_metadata()
    if release['sha'] == current:
        return False
    built = this.get('builtAtUtc', '')
    if isinstance(built, str) and built:
        # Do not downgrade a locally newer build when an older release is
        # temporarily marked latest by GitHub or a stale desktop shortcut.
        try:
            if datetime.fromisoformat(release['publishedAt'].replace('Z', '+00:00')) <= \
                    datetime.fromisoformat(built.replace('Z', '+00:00')):
                return False
        except ValueError:
            return False
    bad = _read_json(_base() / 'portable-failed.json')
    if bad.get('sha') == release['sha']:
        try:
            if (datetime.now(timezone.utc) -
                datetime.fromisoformat(bad['at'])).total_seconds() < 12 * 3600:
                return False
        except (ValueError, TypeError):
            pass
    existing = _read_json(_state_file())
    if (existing.get('schema') == READY_SCHEMA and
        existing.get('sha') == release['sha'] and
        existing.get('digest') == release['digest'] and
        (_version_dir(release['sha']) / 'MABStudio' / 'MABStudio.exe').is_file()):
        return False
    parent = _base() / _ROOT
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.stage-', dir=parent) as temporary:
        stage = Path(temporary)
        archive = stage / 'download.zip'
        _fetch_zip(release, archive)
        _validate_zip(archive)
        extracted = stage / 'extracted'
        with zipfile.ZipFile(archive) as z:
            z.extractall(extracted)
        exe = extracted / 'MABStudio' / 'MABStudio.exe'
        if _manifest(exe).get('studioGitSha') != release['sha']:
            raise ValueError('UPDATE_EXE_RELEASE_SHA_MISMATCH')
        # A release signed only by GitHub's asset digest and manifest is still
        # not allowed to ship a CI-only Mesa renderer to real GPUs.
        for dll in (extracted / 'MABStudio' / '_internal').rglob('*.dll'):
            stem = dll.stem.lower()
            if stem.startswith(('libgallium', 'osmesa', 'libglapi', 'opengl32',
                                'libegl', 'libglesv2', 'mesa', 'gallium')):
                raise ValueError('UPDATE_CI_GRAPHICS_DLL_INCLUDED')
        destination = _version_dir(release['sha'])
        if not destination.exists():
            os.replace(extracted, destination)
        atomic_json(_state_file(), {
            'schema': READY_SCHEMA, 'sha': release['sha'],
            'digest': release['digest'], 'publishedAt': release['publishedAt'],
            'exeRelativePath': 'MABStudio/MABStudio.exe',
        })
    return True


def prepare_in_background() -> None:
    """Safe to call from Tk startup: no Tk calls on this worker thread."""
    if not getattr(sys, 'frozen', False):
        return
    def worker():
        try:
            prepare_update()
        except Exception:
            # Network/update failure must not prevent opening the installed GUI.
            return
    threading.Thread(target=worker, daemon=True,
                     name='MABStudio-portable-release-check').start()


def launch_ready_at_startup() -> bool:
    """Switch only before opening a GUI; keep the old app on any failure."""
    if not getattr(sys, 'frozen', False) or os.environ.get('MAB_STUDIO_DISABLE_AUTO_UPDATE') == '1':
        return False
    ready = _read_json(_state_file())
    if (ready.get('schema') != READY_SCHEMA or
        not isinstance(ready.get('sha'), str) or
        not re.fullmatch(r'[a-f0-9]{40}', ready['sha']) or
        not isinstance(ready.get('digest'), str) or
        not SHA_RE.fullmatch(ready['digest']) or
        ready.get('exeRelativePath') != 'MABStudio/MABStudio.exe'):
        return False
    source = _manifest(Path(sys.executable))
    if source.get('studioGitSha') == ready['sha']:
        return False
    if source.get('studioGitSha') not in ('', None) and source.get('builtAtUtc'):
        try:
            release_date = datetime.fromisoformat(ready['publishedAt'].replace('Z', '+00:00'))
            build_date = datetime.fromisoformat(source['builtAtUtc'].replace('Z', '+00:00'))
            if release_date <= build_date:
                return False
        except (ValueError, KeyError, AttributeError):
            return False
    target = _version_dir(ready['sha']) / 'MABStudio' / 'MABStudio.exe'
    if not target.is_file() or _manifest(target).get('studioGitSha') != ready['sha']:
        return False
    # Native read-only framebuffer test catches broken DLLs before switching.
    try:
        with tempfile.TemporaryDirectory(prefix='mab-updater-check-') as temp:
            report = Path(temp) / 'native.json'
            p = subprocess.run([str(target), '--native-selfcheck', '--report', str(report)],
                               cwd=str(target.parent), timeout=90,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if p.returncode != 0 or _read_json(report).get('result') != 'PASS_NATIVE_READONLY_SELF_CHECK':
                raise ValueError('UPDATE_TARGET_NATIVE_TEST_FAILED')
        child = subprocess.Popen([str(target)], cwd=str(target.parent),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        time.sleep(5)
        if child.poll() is not None:
            raise ValueError('UPDATE_TARGET_EXITS_AT_STARTUP')
        return True
    except (OSError, ValueError, subprocess.TimeoutExpired):
        try:
            atomic_json(_base() / 'portable-failed.json', {
                'sha': ready['sha'], 'at': datetime.now(timezone.utc).isoformat(),
            })
        except OSError:
            pass
        return False
