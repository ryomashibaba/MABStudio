"""Publish a V11-authored *compatible* SOURCE to Studio's read-only inbox.

This is a producer-side handoff helper, not a geometry converter, model runner,
or another architecture engine.  The frozen Studio B143 compiler must accept
and compile the exact design before publication.  Studio still imports it as
an unapproved proposal; publication never changes a saved project or a world.

Usage from a trusted producer workflow (not from the Studio GUI):
    python -m app.v11_publisher --source B143_DESIGN.json

The installed verified engine and the configured sync folder (or local inbox)
are discovered automatically.  Explicit --engine/--inbox overrides are for QA.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

from app.core import EngineManager, StudioError, run_bridge, user_root
from app.v11_source_import import MAX_SOURCE_JSON_BYTES, read_external_b143_source
from app.v11_watch import READY_NAME, candidate_directories


def publish_compatible_v11_source(source_file: str | Path, engine: str | Path,
                                  inbox: str | Path) -> dict:
    """Validate using Studio's pinned real B143, then atomically publish JSON.

    Does not expand repeat/staircase, recompile geometry independently, or trust
    V11-provided compiled voxels/receipts.  Source and old published data remain
    unchanged if the new candidate is invalid or the compilation fails.
    """
    source = Path(source_file)
    folder = Path(inbox)
    if not folder.is_dir() or folder.is_symlink():
        raise StudioError('V11受け渡し先は実在する通常のフォルダを指定してください')
    target = folder / READY_NAME
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise StudioError('V11受け渡しファイルが通常のファイルではありません')

    # Extract only the data portion of safe V11 product wrappers.  Any enriched
    # grammar is rejected by the existing importer, before any file writes.
    design = read_external_b143_source(source)
    compiled = run_bridge(Path(engine), design)  # SHA-pinned authoritative B143
    if (compiled.get('sourceSha256') is None or
        compiled.get('designOnly') is not True or
        compiled.get('readiness') != 'NOT_PREPARED' or
        compiled.get('worldContacts') != 0 or
        compiled.get('BuildPlan', {}).get('operations') != []):
        raise StudioError('固定B143の未施工SOURCE証跡が不正です')

    content = (json.dumps(design, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    if len(content) > MAX_SOURCE_JSON_BYTES:
        raise StudioError('受け渡し用SOURCEが1MiBを超えています')
    sha = hashlib.sha256(content).hexdigest()
    if (target.is_file() and target.stat().st_size == len(content) and
            hashlib.sha256(target.read_bytes()).hexdigest() == sha):
        return {'published': False, 'sourceSha256': compiled['sourceSha256'],
                'fileSha256': sha, 'voxels': len(compiled.get('primitives', []))}

    temporary = None
    try:
        with tempfile.NamedTemporaryFile('wb', prefix='.v11-publish-', suffix='.json',
                                         dir=folder, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, target)  # same-volume atomic publish; no partial reads
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {'published': True, 'sourceSha256': compiled['sourceSha256'],
            'fileSha256': sha, 'voxels': len(compiled.get('primitives', []))}


def main() -> int:
    p = argparse.ArgumentParser(description='Publish V11 compatible SOURCE only')
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--engine', type=Path, help='Verified B143 directory; defaults to Studio active engine')
    p.add_argument('--inbox', type=Path, help='Existing trusted sync folder; defaults to Studio inbox')
    args = p.parse_args()
    try:
        engine = args.engine
        if engine is None:
            engine = EngineManager().active_path
            if engine is None:
                raise StudioError('Studioの検証済みB143がまだ導入されていません')
        inbox = args.inbox
        if inbox is None:
            root = user_root()
            feeds = [p for p in candidate_directories(root)[1:] if p.is_dir() and not p.is_symlink()]
            inbox = feeds[0] if feeds else root / 'v11-inbox'
            inbox.mkdir(parents=True, exist_ok=True)
        result = publish_compatible_v11_source(args.source, engine, inbox)
    except (StudioError, OSError, ValueError) as exc:
        p.exit(2, 'V11 SOURCE not published: ' + str(exc) + '\n')
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
