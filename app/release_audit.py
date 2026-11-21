"""Offline audit for the onedir Windows app bundle, not Minecraft certification.

A build MUST exercise its private Node transport before publishing. The resulting
manifest detects incomplete/changed runtime assets on subsequent starts; it is
not a cryptographic signature or third-party license clearance.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
import sys

from app.core import StudioError
from app.education import LIVE_BRIDGE_SOURCE_SHA256
from app.education_build import BUILD_BRIDGE_SHA256

SCHEMA = 'mab-studio-critical-runtime-manifest-v1'
MANIFEST = 'RUNTIME_MANIFEST.json'
BASE_FILES = (
    'MABStudio.exe', 'node.exe', 'NODE_LICENSE.txt',
    '_internal/app/engine_bridge.mjs',
    '_internal/app/gpu_viewer_assets/viewer.html',
    '_internal/app/gpu_viewer_assets/viewer.js',
)
RUNTIME_FOLDER = '_internal/app/education_runtime'
MAX_ASSETS = 4000
MAX_ASSET_BYTES = 150 * 1024 * 1024


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for part in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def _reject_ci_graphics_dlls(root: Path) -> None:
    """Fail closed if CI's software OpenGL driver was bundled for user PCs.

    A GitHub runner uses temporary Mesa OSMesa for headless pixel verification.
    That driver must never override the user's native GPU OpenGL driver.
    """
    internal = root / '_internal'
    if not internal.is_dir():
        raise StudioError('配布版の同梱データがありません')
    names = ('libgallium', 'osmesa', 'libglapi', 'opengl32',
             'libegl', 'libglesv2', 'mesa', 'gallium')
    for item in internal.rglob('*'):
        if item.is_file() and item.suffix.casefold() == '.dll':
            base = item.stem.casefold()
            if base.startswith(names):
                raise StudioError('配布版に検証専用の描画DLLが混入しています: '
                                  + item.name[:100])


def _runtime_files(root: Path) -> list[str]:
    """Inventory a bounded, deterministic set of essential shipped assets."""
    _reject_ci_graphics_dlls(root)
    keys = set(BASE_FILES)
    runtime = root / RUNTIME_FOLDER
    if not runtime.is_dir() or runtime.is_symlink():
        raise StudioError('Education同梱ランタイムが見つかりません')
    for path in runtime.rglob('*'):
        if path.is_symlink():
            raise StudioError('配布版のランタイムにリンクが含まれています')
        if path.is_file():
            keys.add(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise StudioError('配布版に通常ファイル以外が含まれています')
        if len(keys) > MAX_ASSETS:
            raise StudioError('配布版のファイル数が安全上限を超えました')
    for key in keys:
        file = root / key
        if file.is_symlink() or not file.is_file() or file.stat().st_size == 0:
            raise StudioError('配布用の必須部品が欠損しています: ' + key[:160])
        if file.stat().st_size > MAX_ASSET_BYTES:
            raise StudioError('配布版の部品が大きすぎます: ' + key[:160])
    required = {
        f'{RUNTIME_FOLDER}/bridge.mjs',
        f'{RUNTIME_FOLDER}/build.mjs',
        f'{RUNTIME_FOLDER}/package.json',
        f'{RUNTIME_FOLDER}/node_modules/socket-be/package.json',
    }
    if not required.issubset(keys):
        raise StudioError('Education読取用のSocketBE部品が不足しています')
    return sorted(keys)


def _check_transport_metadata(root: Path) -> None:
    folder = root / RUNTIME_FOLDER
    try:
        pkg = json.loads((folder / 'node_modules/socket-be/package.json').read_text(encoding='utf-8'))
        top = json.loads((folder / 'package.json').read_text(encoding='utf-8'))
    except (ValueError, OSError, UnicodeError) as exc:
        raise StudioError('Educationランタイムのpackage.jsonが不正です') from exc
    if pkg.get('version') != '2.8.0' or top.get('dependencies', {}).get('socket-be') != '2.8.0':
        raise StudioError('同梱SocketBEが検証対象の2.8.0と一致しません')
    if _digest(folder / 'bridge.mjs') != LIVE_BRIDGE_SOURCE_SHA256:
        raise StudioError('Education読み取り専用コードのSHAが一致しません')
    if _digest(folder / 'build.mjs') != BUILD_BRIDGE_SHA256:
        raise StudioError('Education施工コードのSHAが一致しません')


def build_manifest(root: Path, *, exercise: bool = True) -> dict:
    """Create in-memory manifest; only `exercise=True` is used for real builds."""
    root = Path(root).resolve()
    names = _runtime_files(root)
    _check_transport_metadata(root)
    version = 'NOT_EXECUTED_IN_UNIT_TEST'
    if exercise:
        node = str(root / 'node.exe')
        def run(args, *, cwd=None):
            try:
                result = subprocess.run([node, *args], cwd=cwd, capture_output=True,
                                        text=True, timeout=15, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise StudioError('同梱Node.jsの起動テストに失敗しました') from exc
            if result.returncode != 0:
                raise StudioError('配布版Node.jsの実行検証に失敗: '+(result.stderr or result.stdout)[-250:])
            return result.stdout.strip()
        version = run(['--version'])
        m = re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)', version)
        if not m or int(m.group(1)) < 22:
            raise StudioError('Node.js 22以上が必要です（同梱版）')
        run(['--permission', '-e',
             'if (!process.permission || typeof process.permission.has!=="function") process.exit(3)'])
        run([str(root / RUNTIME_FOLDER / 'bridge.mjs'), '--self-test'])
        run([str(root / RUNTIME_FOLDER / 'build.mjs'), '--self-test'])
        run(['--input-type=module', '-e',
             'import("socket-be").then(m=>{if(typeof m.Server!=="function" || !m.ServerEvent)process.exit(4)}).catch(e=>{console.error(e);process.exit(5)})'],
             cwd=str(root / RUNTIME_FOLDER))
    release_sha = os.environ.get('GITHUB_SHA', '')
    release_identity = {}
    if re.fullmatch(r'[a-f0-9]{40}', release_sha):
        release_identity = {
            'studioGitSha': release_sha,
            'builtAtUtc': datetime.now(timezone.utc).isoformat(),
        }
    return {
        'schema': SCHEMA,
        'nodeVersionAtBuild': version,
        'socketBeVersion': '2.8.0',
        'assets': {name: _digest(root / name) for name in names},
        **release_identity,
        'meaning': 'Critical runtime file integrity only; not Windows execution, model, world or licensing certification',
    }


def verify_manifest(root: Path) -> dict:
    root = Path(root).resolve()
    file = root / MANIFEST
    if file.is_symlink() or not file.is_file() or file.stat().st_size > 600_000:
        raise StudioError('配布版の整合性記録がありません、または不正です')
    try:
        data = json.loads(file.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError, OSError) as exc:
        raise StudioError('配布版の整合性記録を読めません') from exc
    if not isinstance(data, dict) or data.get('schema') != SCHEMA or not isinstance(data.get('assets'), dict):
        raise StudioError('配布版の整合性記録の形式が不正です')
    names = _runtime_files(root)
    if sorted(data['assets']) != names:
        raise StudioError('配布版の構成がビルド時と異なります。フォルダ全体を再展開してください')
    for name in names:
        expected = data['assets'][name]
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected) or _digest(root / name) != expected:
            raise StudioError('配布版の同梱部品が変更されています: '+name[:160])
    _check_transport_metadata(root)
    return {'criticalAssets': len(names), 'bundleManifestVerified': True}


def write_manifest(root: Path) -> dict:
    root = Path(root).resolve()
    data = build_manifest(root, exercise=True)
    dest = root / MANIFEST
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    verify_manifest(root)
    return {'assets': len(data['assets']), 'nodeVersion': data['nodeVersionAtBuild']}


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python -m app.release_audit DIST_MABSTUDIO_DIRECTORY')
    try:
        print(json.dumps(write_manifest(Path(sys.argv[1])), ensure_ascii=False))
    except (StudioError, OSError, ValueError) as exc:
        print('MAB STUDIO DISTRIBUTION AUDIT FAILED: '+str(exc), file=sys.stderr)
        raise SystemExit(1)
