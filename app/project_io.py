"""Explicit, verified SOURCE design export. Not a Minecraft execution interface."""
from __future__ import annotations
import json
import os
from pathlib import Path
from app.core import StudioError, SCHEMA


def export_verified_source(filename, design, source_sha: str,
                           geometry_sha: str, compile_with_mab) -> Path:
    """Recompile the approved design and create a new file without overwriting.

    The provided compiler must be the existing SHA-pinned MAB bridge; this
    module never recreates B143 geometry or authorizes world writes.
    """
    dest=Path(filename).expanduser().resolve()
    if dest.suffix.lower()!='.json':
        raise StudioError('SOURCE設計は.jsonとしてのみ保存できます')
    if not isinstance(design,dict) or design.get('schema')!=SCHEMA:
        raise StudioError('SOURCE形式が不正です')
    actual=compile_with_mab(design)
    if (actual.get('sourceSha256')!=source_sha or
            actual.get('geometrySha256')!=geometry_sha or
            actual.get('designOnly') is not True or
            actual.get('readiness')!='NOT_PREPARED' or
            actual.get('worldContacts')!=0 or
            actual.get('worldCoordinates') is not None or
            actual.get('stats',{}).get('constructionApproved') is not False or
            actual.get('BuildPlan',{}).get('operations')!=[]):
        raise StudioError('表示中のSOURCEとMAB再検証結果が一致しません。保存を中止しました')
    # Exclusive creation: never silently overwrite a user's earlier building.
    # A partially written *new* export is removed if writing fails.
    dest.parent.mkdir(parents=True,exist_ok=True)
    created=False
    try:
        with dest.open('x',encoding='utf-8') as f:
            created=True
            json.dump(design,f,ensure_ascii=False,indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
    except FileExistsError as exc:
        raise StudioError('同じ名前の設計が既にあります。別のファイル名を選んでください') from exc
    except (OSError, ValueError):
        if created:
            dest.unlink(missing_ok=True)
        raise
    return dest
