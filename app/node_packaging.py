"""Safely package installed Node.js with its matching upstream license notice.

The Windows Node MSI is not required to keep LICENSE beside node.exe.  If it
is absent, download the *same release tag's* official Node.js LICENSE from
nodejs/node over HTTPS. Never omit the notice or substitute an unrelated one.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.request import urlopen

VERSION = re.compile(r'^v(\d+)\.(\d+)\.(\d+)$')
MAX_LICENSE_BYTES = 2 * 1024 * 1024
RELEASE_REPOSITORY = 'https://raw.githubusercontent.com/nodejs/node/'


class NodePackageError(Exception):
    pass


def validate_version(raw: str) -> str:
    match = VERSION.fullmatch(raw.strip())
    if not match or int(match.group(1)) < 22:
        raise NodePackageError(f'UNSUPPORTED_NODE_VERSION: {raw[:80]!r} (Node 22+ required)')
    return raw.strip()


def _valid_license(blob: bytes) -> bool:
    if not 500 <= len(blob) <= MAX_LICENSE_BYTES:
        return False
    try:
        value = blob.decode('utf-8')
    except UnicodeError:
        return False
    # An official Node license has a Node.js copyright and dependency notices.
    # Validate a minimal recognizable structure, rather than treating an error
    # page or empty response as a license.
    return value.startswith('Node.js is licensed for use as follows:') and 'Copyright Node.js contributors' in value


def _read_local_license(node: Path) -> bytes | None:
    folders = (node.parent, node.parent.parent) if node.parent.name.lower() == 'bin' else (node.parent,)
    for folder in folders:
        for name in ('LICENSE', 'LICENSE.txt', 'LICENSE.md'):
            candidate = folder / name
            if candidate.is_file() and not candidate.is_symlink():
                blob = candidate.read_bytes()
                if _valid_license(blob):
                    return blob
    return None


def _upstream_license(version: str) -> bytes:
    # Release tags are syntax checked above; no user-controlled URL or host.
    url = RELEASE_REPOSITORY + version + '/LICENSE'
    try:
        with urlopen(url, timeout=25) as response:
            if response.geturl() != url:
                raise NodePackageError('NODE_LICENSE_REDIRECT_REJECTED')
            blob = response.read(MAX_LICENSE_BYTES + 1)
    except NodePackageError:
        raise
    except Exception as exc:
        raise NodePackageError(
            'NODE_LICENSE_DOWNLOAD_FAILED: official matching-version license unavailable; '
            'do not package without license (requires network access)'
        ) from exc
    if not _valid_license(blob):
        raise NodePackageError('NODE_LICENSE_UPSTREAM_INVALID')
    return blob


def package_node(dest: Path, *, node_path: Path | None = None) -> dict:
    if node_path is None:
        located = shutil.which('node.exe') or shutil.which('node')
        if not located:
            raise NodePackageError('NODE_EXE_NOT_FOUND: install Node.js 22+ to build Studio')
        node_path = Path(located)
    node = node_path.resolve(strict=True)
    if not node.is_file():
        raise NodePackageError('NODE_EXE_NOT_FILE')
    try:
        result = subprocess.run([str(node), '--version'], capture_output=True,
                                text=True, check=False, timeout=12)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NodePackageError('NODE_VERSION_CHECK_FAILED') from exc
    if result.returncode != 0:
        raise NodePackageError('NODE_VERSION_CHECK_FAILED')
    version = validate_version(result.stdout)
    license_bytes = _read_local_license(node)
    source = 'installed-directory'
    if license_bytes is None:
        license_bytes = _upstream_license(version)
        source = 'official-matching-release'
    dest = dest.resolve()
    if not dest.is_dir():
        raise NodePackageError('DIST_FOLDER_MISSING')
    # Prepare copies under private filenames. Do not publish an incomplete pair.
    node_tmp = dest / 'node.exe.mab-new'
    notice_tmp = dest / 'NODE_LICENSE.txt.mab-new'
    try:
        shutil.copyfile(node, node_tmp)
        notice_tmp.write_bytes(license_bytes)
        if node_tmp.stat().st_size < 100_000 or not _valid_license(notice_tmp.read_bytes()):
            raise NodePackageError('NODE_COPY_OR_LICENSE_INVALID')
        os.replace(node_tmp, dest / 'node.exe')
        os.replace(notice_tmp, dest / 'NODE_LICENSE.txt')
    finally:
        node_tmp.unlink(missing_ok=True)
        notice_tmp.unlink(missing_ok=True)
    return {'nodeVersion': version, 'licenseSource': source,
            'licenseBytes': len(license_bytes), 'packaged': True}


def main() -> int:
    parser = argparse.ArgumentParser(description='Package Node.js with matching LICENSE')
    parser.add_argument('distribution_directory', type=Path)
    args = parser.parse_args()
    try:
        info = package_node(args.distribution_directory)
    except (NodePackageError, OSError) as exc:
        print(f'NODE PACKAGE FAILED: {exc}', file=sys.stderr)
        return 1
    print(f"Node {info['nodeVersion']} packaged with LICENSE ({info['licenseSource']}).")
    return 0


if __name__ == '__main__':
    sys.exit(main())
