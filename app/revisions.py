"""Source-only, append-only project revisions and exact conceptual voxel differences.

No world placement coordinates, Minecraft command generation, or world writes.
Only MAB's compiled scene primitives are compared; this is NOT an execution plan.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from app.core import StudioError, atomic_json, json_read, SCHEMA

PROJECT_SCHEMA = 'mab-studio-local-revisions-v1'
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
HEX32 = re.compile(r'[0-9a-f]{32}\Z')
MAX_CELLS = 160000


def _digest(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()


def _cells(scene: dict) -> dict[tuple[int, int, int], str]:
    if not isinstance(scene, dict) or scene.get('designOnly') is not True or scene.get('readiness') != 'NOT_PREPARED':
        raise StudioError('施工不可のMAB SOURCEモデル以外は扱えません')
    if (scene.get('worldCoordinates') is not None or scene.get('worldContacts') != 0
            or scene.get('BuildPlan', {}).get('operations') != []
            or scene.get('stats', {}).get('constructionApproved') is not False):
        raise StudioError('MABの施工禁止条件が一致しません')
    palette = scene.get('layerNames')
    raw = scene.get('primitives')
    if not isinstance(palette, list) or not palette or len(set(palette)) != len(palette) or not all(isinstance(p, str) for p in palette):
        raise StudioError('MABのマテリアル一覧が不正です')
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_CELLS:
        raise StudioError('MABのセル数が不正です')
    result = {}
    for p in raw:
        if (not isinstance(p, list) or len(p) != 5 or
                not all(type(x) is int for x in p) or not 0 <= p[3] < len(palette)):
            raise StudioError('MABのプリミティブ形式が不正です')
        xyz = tuple(p[:3])
        if xyz in result:
            raise StudioError('MABの重複セルを検出しました')
        result[xyz] = palette[p[3]]
    if scene.get('stats', {}).get('voxels') != len(raw):
        raise StudioError('MABのセル数がレポートと一致しません')
    if not all(isinstance(scene.get(name), str) and HEX64.fullmatch(scene[name]) for name in ('sourceSha256', 'geometrySha256')):
        raise StudioError('SOURCEのダイジェストが不正です')
    return result


def compare_scenes(old: dict, new: dict) -> dict:
    """Return a bounded in-memory design delta. No placement, no world coordinates."""
    previous, current = _cells(old), _cells(new)
    added = []
    removed = []
    replaced = []
    for pos in sorted(previous.keys() | current.keys()):
        before, after = previous.get(pos), current.get(pos)
        if before is None:
            added.append([*pos, after])
        elif after is None:
            removed.append([*pos, before])
        elif before != after:
            replaced.append([*pos, before, after])
    unchanged = len(current) - len(added) - len(replaced)
    assert unchanged >= 0
    return {
        'schema': 'mab-studio-concept-diff-v1',
        'sourceOnly': True, 'worldWriteAllowed': False, 'worldCoordinates': None,
        'oldGeometrySha256': old['geometrySha256'], 'newGeometrySha256': new['geometrySha256'],
        'counts': {'added': len(added), 'removed': len(removed), 'replaced': len(replaced),
                   'unchanged': unchanged},
        'added': added, 'removed': removed, 'replaced': replaced,
    }


def diff_preview_scene(delta: dict, context_scene: dict | None = None) -> dict | None:
    """Visually overlay changes on an optional muted source model, never a BuildPlan."""
    changed = {(p[0],p[1],p[2]):0 for p in delta['added']}
    changed.update({(p[0],p[1],p[2]):1 for p in delta['removed']})
    changed.update({(p[0],p[1],p[2]):2 for p in delta['replaced']})
    if not changed:
        return None
    positions = {}
    if context_scene is not None:
        _cells(context_scene)
        positions.update({tuple(p[:3]):3 for p in context_scene['primitives']})
    positions.update(changed)
    pts = [[*pos,mat,0] for pos,mat in positions.items()]
    cols = list(zip(*(p[:3] for p in pts)))
    return {'primitives': pts, 'bounds': dict(zip(('minX', 'minY', 'minZ'), (min(c) for c in cols))) |
            dict(zip(('maxX', 'maxY', 'maxZ'), (max(c) for c in cols))),
            'previewPalette': ['#42d88b', '#ef6768', '#f8bf54', '#3a5767'],
            'priorityPositions': [list(p) for p in changed],
            'previewLabel': f'未施工の変更候補  +{delta["counts"]["added"]} / -{delta["counts"]["removed"]} / 交換{delta["counts"]["replaced"]}',
            'previewOnly': True}


class RevisionStore:
    """Write-once version snapshots with SHA-256 checked, atomic head pointer.

    A rollback creates a NEW revision. It never deletes history or changes a world.
    """
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.index = self.folder / 'project.json'

    def _read_index(self) -> dict | None:
        info = json_read(self.index)
        if info is None:
            return None
        if (not isinstance(info, dict) or info.get('schema') != PROJECT_SCHEMA
                or not isinstance(info.get('project'), str) or not HEX32.fullmatch(info['project'])
                or not isinstance(info.get('head'), str) or not HEX32.fullmatch(info['head'])):
            raise StudioError('プロジェクトの履歴インデックスが不正です')
        return info

    def read_revision(self, revision_id: str) -> dict:
        if not isinstance(revision_id, str) or not HEX32.fullmatch(revision_id):
            raise StudioError('履歴IDが不正です')
        data = json_read(self.folder / 'revisions' / (revision_id + '.json'))
        if not isinstance(data, dict) or data.get('schema') != PROJECT_SCHEMA or data.get('revisionId') != revision_id:
            raise StudioError('履歴データが存在しないか不正です')
        expected = data.get('snapshotSha256')
        payload = {k: v for k, v in data.items() if k != 'snapshotSha256'}
        if expected != _digest(payload):
            raise StudioError('履歴データの改変を検出しました')
        if not isinstance(data.get('design'), dict) or data['design'].get('schema') != SCHEMA:
            raise StudioError('保存した設計形式が不正です')
        _cells(data.get('scene'))
        if data['scene']['sourceSha256'] != data.get('sourceSha256'):
            raise StudioError('保存したSOURCE SHAが一致しません')
        return data

    def head(self) -> dict | None:
        info = self._read_index()
        if info is None:
            return None
        item = self.read_revision(info['head'])
        if item['projectId'] != info['project']:
            raise StudioError('プロジェクト履歴の紐付けが不正です')
        return item

    def commit(self, design: dict, scene: dict, *, engine_commit: str,
               operation: str = 'new', previous_id: str | None = None) -> dict:
        _cells(scene)
        if not isinstance(design, dict) or design.get('schema') != SCHEMA:
            raise StudioError('設計形式が不正です')
        if not isinstance(engine_commit, str) or not re.fullmatch('[a-f0-9]{40}', engine_commit):
            raise StudioError('エンジンのコミットが不正です')
        if operation not in ('new', 'revision', 'restore'):
            raise StudioError('不正な履歴操作です')
        old = self.head()
        if operation == 'new':
            project_id = uuid4().hex
            parent = None
            prev_scene = None
        else:
            if old is None or old['revisionId'] != previous_id:
                raise StudioError('履歴の更新競合を検出しました')
            project_id = old['projectId']
            parent = old['revisionId']
            prev_scene = old['scene']
        delta_counts = None if prev_scene is None else compare_scenes(prev_scene, scene)['counts']
        revision_id = uuid4().hex
        data = dict(schema=PROJECT_SCHEMA, projectId=project_id, revisionId=revision_id,
                    parentId=parent, operation=operation, engineCommit=engine_commit,
                    sourceSha256=scene['sourceSha256'], geometrySha256=scene['geometrySha256'],
                    design=design, scene=scene, diffCounts=delta_counts,
                    constructionApproved=False, worldWrites=0, worldCoordinates=None)
        data['snapshotSha256'] = _digest(data)
        revisions = self.folder / 'revisions'
        revisions.mkdir(parents=True, exist_ok=True)
        # Immutable snapshots; index is updated only after the snapshot exists.
        dest = revisions / (revision_id + '.json')
        with dest.open('x', encoding='utf-8') as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write('\n')
            file.flush()
        atomic_json(self.index, {'schema': PROJECT_SCHEMA, 'project': project_id, 'head': revision_id})
        return data

    def restore_previous(self, *, engine_commit: str, verify=None) -> dict:
        current = self.head()
        if current is None or current['parentId'] is None:
            raise StudioError('戻せる前の設計がありません')
        previous = self.read_revision(current['parentId'])
        if previous['projectId'] != current['projectId']:
            raise StudioError('異なるプロジェクトの履歴を復元できません')
        if verify is None:
            raise StudioError('復元時には真正MABでの再検証が必要です')
        scene = verify(previous['design'])
        if (scene['geometrySha256'] != previous['geometrySha256']
                or scene['sourceSha256'] != previous['sourceSha256']):
            raise StudioError('保存時と復元時のMAB出力が一致しません')
        return self.commit(previous['design'], scene, engine_commit=engine_commit,
                           operation='restore', previous_id=current['revisionId'])
