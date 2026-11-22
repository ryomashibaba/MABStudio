"""Explicit, verified SOURCE design export. Not a Minecraft execution interface."""
from __future__ import annotations
import json
import hashlib
import os
from pathlib import Path
import re
from app.core import BASELINE_SHA, StudioError, SCHEMA
from app.revisions import normalize_edit_scope

UNAPPROVED_SCHEMA = 'mab-studio-unapproved-draft-v1'
_HEX64 = re.compile(r'[0-9a-f]{64}\Z')


def _canonical_sha256(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def save_unapproved_draft(filename, design, metadata, source_sha: str,
                          geometry_sha: str, compile_with_mab) -> Path:
    """Save a verifiable review package outside RevisionStore history."""
    dest = Path(filename).expanduser().resolve()
    if dest.suffix.lower() != '.json':
        raise StudioError('未承認案は.jsonとしてのみ保存できます')
    if not isinstance(design, dict) or design.get('schema') != SCHEMA:
        raise StudioError('SOURCE形式が不正です')
    if not isinstance(metadata, dict):
        raise StudioError('未承認案の保存情報が不正です')
    actual = compile_with_mab(design)
    _check_compiled_source(actual, source_sha, geometry_sha)
    required = {'draftKind', 'origin', 'engineSha', 'expectedHead',
                'expectedHeadSnapshotSha256'}
    if not required <= metadata.keys():
        raise StudioError('未承認案の復元情報が不足しています')
    if metadata['draftKind'] not in ('new', 'revision'):
        raise StudioError('未承認案の種類が不正です')
    if metadata['origin'] not in ('new-ai', 'v11-external', 'offline-sample', 'revision'):
        raise StudioError('未承認案の由来が不正です')
    if metadata['engineSha'] != BASELINE_SHA:
        raise StudioError('固定B143 SHAが一致しません')
    if (metadata['expectedHead'] is not None
            and (not isinstance(metadata['expectedHead'], str)
                 or not re.fullmatch(r'[0-9a-f]{32}', metadata['expectedHead']))):
        raise StudioError('承認済み履歴HEADが不正です')
    if metadata['expectedHeadSnapshotSha256'] is not None and not _HEX64.fullmatch(
            str(metadata['expectedHeadSnapshotSha256'])):
        raise StudioError('承認済み履歴HEAD SHAが不正です')
    if (metadata['expectedHead'] is None) != (metadata['expectedHeadSnapshotSha256'] is None):
        raise StudioError('承認済み履歴HEAD情報が不整合です')
    metadata = dict(metadata)
    if 'editScope' in metadata:
        metadata['editScope'] = normalize_edit_scope(metadata['editScope'])
    if (metadata['draftKind'] == 'revision') != (metadata['origin'] == 'revision'):
        raise StudioError('未承認案の種類と由来が一致しません')
    if metadata['draftKind'] == 'revision':
        if (metadata.get('parentId') != metadata['expectedHead']
                or not isinstance(metadata.get('parentGeometrySha256'), str)
                or not _HEX64.fullmatch(metadata['parentGeometrySha256'])):
            raise StudioError('修正案の承認済み基準が不正です')
    package = {
        'schema': UNAPPROVED_SCHEMA,
        'status': 'UNAPPROVED',
        'metadata': {**metadata,
                     'sourceSha256': source_sha,
                     'geometrySha256': geometry_sha,
                     'designSha256': _canonical_sha256(design)},
        'design': design,
    }
    package['packageSha256'] = _canonical_sha256(package)
    dest.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with dest.open('x', encoding='utf-8') as handle:
            created = True
            json.dump(package, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise StudioError('同名の未承認案が既にあります。既存ファイルは上書きしません') from exc
    except (OSError, ValueError):
        if created:
            dest.unlink(missing_ok=True)
        raise
    return dest


def read_unapproved_draft(filename) -> tuple[dict, dict]:
    """Read an intact draft package; callers must still recompile with B143."""
    source = Path(filename).expanduser().resolve()
    try:
        if source.stat().st_size > 2 * 1024 * 1024:
            raise StudioError('未承認案が2MiBを超えています')
        package = json.loads(source.read_text(encoding='utf-8-sig'))
    except StudioError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StudioError('未承認案ファイルを読み込めないか、破損しています: ' + str(exc)) from exc
    if (not isinstance(package, dict) or package.get('schema') != UNAPPROVED_SCHEMA
            or package.get('status') != 'UNAPPROVED'):
        raise StudioError('未承認案パッケージではありません')
    expected = package.get('packageSha256')
    unsigned = {k: v for k, v in package.items() if k != 'packageSha256'}
    if not isinstance(expected, str) or not _HEX64.fullmatch(expected) or _canonical_sha256(unsigned) != expected:
        raise StudioError('未承認案ファイルの改変または破損を検出しました')
    design, metadata = package.get('design'), package.get('metadata')
    if not isinstance(design, dict) or design.get('schema') != SCHEMA or not isinstance(metadata, dict):
        raise StudioError('未承認案のSOURCEまたはメタデータが不正です')
    if metadata.get('designSha256') != _canonical_sha256(design):
        raise StudioError('SOURCEの内容改変を検出しました')
    if metadata.get('engineSha') != BASELINE_SHA:
        raise StudioError('固定B143 SHAが一致しません。復元をHOLDしました')
    if (metadata.get('draftKind') not in ('new', 'revision')
            or metadata.get('origin') not in ('new-ai', 'v11-external', 'offline-sample', 'revision')
            or not all(isinstance(metadata.get(k), str) and _HEX64.fullmatch(metadata[k])
                       for k in ('sourceSha256', 'geometrySha256'))):
        raise StudioError('未承認案の検証情報が不正です')
    expected_head = metadata.get('expectedHead')
    if expected_head is not None and (not isinstance(expected_head, str)
                                      or not re.fullmatch(r'[0-9a-f]{32}', expected_head)):
        raise StudioError('承認済み履歴HEADが不正です')
    head_sha = metadata.get('expectedHeadSnapshotSha256')
    if head_sha is not None and (not isinstance(head_sha, str) or not _HEX64.fullmatch(head_sha)):
        raise StudioError('承認済み履歴HEAD SHAが不正です')
    if (expected_head is None) != (head_sha is None):
        raise StudioError('承認済み履歴HEAD情報が不整合です')
    if 'editScope' in metadata:
        metadata['editScope'] = normalize_edit_scope(metadata['editScope'])
    if metadata['draftKind'] == 'revision':
        if (metadata.get('origin') != 'revision'
                or not isinstance(metadata.get('parentId'), str)
                or not re.fullmatch(r'[0-9a-f]{32}', metadata['parentId'])
                or not isinstance(metadata.get('parentGeometrySha256'), str)
                or not _HEX64.fullmatch(metadata['parentGeometrySha256'])):
            raise StudioError('修正案の元履歴情報が不正です')
    elif metadata.get('origin') == 'revision':
        raise StudioError('未承認案の種類と由来が一致しません')
    return design, metadata


def _check_compiled_source(actual, source_sha: str, geometry_sha: str) -> None:
    if (not isinstance(actual, dict) or actual.get('sourceSha256') != source_sha
            or actual.get('geometrySha256') != geometry_sha
            or actual.get('designOnly') is not True
            or actual.get('readiness') != 'NOT_PREPARED'
            or actual.get('worldContacts') != 0
            or actual.get('worldCoordinates') is not None
            or actual.get('stats', {}).get('constructionApproved') is not False
            or actual.get('BuildPlan', {}).get('operations') != []):
        raise StudioError('SOURCE/geometry再検証が一致しません。保存・復元を中止しました')


def export_verified_source(filename, design, source_sha: str,
                           geometry_sha: str, compile_with_mab) -> Path:
    """Recompile the design and create a new file without overwriting.

    The provided compiler must be the existing SHA-pinned MAB bridge; this
    module never recreates B143 geometry or authorizes world writes.
    """
    dest=Path(filename).expanduser().resolve()
    if dest.suffix.lower()!='.json':
        raise StudioError('SOURCE設計は.jsonとしてのみ保存できます')
    if not isinstance(design,dict) or design.get('schema')!=SCHEMA:
        raise StudioError('SOURCE形式が不正です')
    actual=compile_with_mab(design)
    _check_compiled_source(actual, source_sha, geometry_sha)
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
