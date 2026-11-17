"""Fail-closed packaged Windows runtime checks, with no world or model access.

This is NOT an EXE launch certification.  The check prevents an onedir app
from silently using tools from a developer's PATH instead of its own files.
"""
from __future__ import annotations

import sys
from pathlib import Path
from app.core import StudioError, bridge_script, node_binary


def verify_packaged_runtime() -> dict:
    """Require every essential runtime asset in the deployed onedir package.

    A source checkout may use a system Node.js as before.  Only frozen Windows
    builds are required to ship a private Node and third-party notices.
    """
    if not getattr(sys, 'frozen', False):
        return {'mode': 'SOURCE', 'packagedRuntimeVerified': False}
    exe = Path(sys.executable).resolve()
    package_dir = exe.parent
    if not (package_dir / 'NODE_LICENSE.txt').is_file():
        raise StudioError('配布EXEのNode.jsライセンス通知がありません。完全な配布フォルダが必要です。')
    node_binary()  # Strictly bundled node.exe in frozen mode.
    if not bridge_script().is_file():
        raise StudioError('配布EXEのMAB接続プログラムがありません。完全な配布フォルダが必要です。')
    bundle = getattr(sys, '_MEIPASS', None)
    if not bundle:
        raise StudioError('配布EXEの同梱データの場所が分かりません。再展開してください。')
    base = Path(bundle).resolve() / 'app' / 'education_runtime'
    if not (base / 'bridge.mjs').is_file() or not (base / 'node_modules' / 'socket-be' / 'package.json').is_file():
        raise StudioError('配布EXEのEducation読み取り専用部品がありません。完全な配布フォルダが必要です。')
    # A published onedir build includes a manifest generated ONLY after the
    # private Node, SocketBE import and read-only self-test have run at build.
    from app.release_audit import verify_manifest
    audit = verify_manifest(package_dir)
    return {'mode': 'PACKAGED_ONEDIR', 'packagedRuntimeVerified': True,
            **audit, 'worldContacts': 0, 'worldWrites': 0}
