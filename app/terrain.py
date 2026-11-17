"""Fail-closed, READ-ONLY .mcworld archive handling and bounded terrain sampling.

Never touches live Minecraft directories. Only opens a disposable ZIP extraction.
The optional Amulet adapter is NOT assumed to support every Education world.
Unknown / unsupported cells are not inferred to be air.
"""
from __future__ import annotations
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import tempfile
import zipfile

from app.core import StudioError, atomic_json
from app.revisions import _cells

MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_EXTRACTED_BYTES = 1400 * 1024 * 1024
MAX_ZIP_MEMBERS = 50000
MAX_SAMPLE_VOLUME = 32 * 32 * 32
DIMENSION = 'minecraft:overworld'


def inspect_mcworld(filename: str | Path) -> dict:
    """Validate untrusted archive paths/sizes; report structure, NOT block parsing."""
    source = Path(filename)
    if source.suffix.lower() != '.mcworld' or not source.is_file():
        raise StudioError('Minecraft Educationからエクスポートした .mcworld を選んでください')
    if source.stat().st_size > MAX_ARCHIVE_BYTES:
        raise StudioError('試作版の上限（512MB）を超えるワールドです')
    try:
        with zipfile.ZipFile(source) as z:
            entries = z.infolist()
            if not 1 <= len(entries) <= MAX_ZIP_MEMBERS:
                raise StudioError('ワールドのファイル件数が安全上限を超えています')
            size = sum(i.file_size for i in entries)
            if size > MAX_EXTRACTED_BYTES:
                raise StudioError('展開後のファイル容量が安全上限を超えています')
            files = {}
            for entry in entries:
                name = entry.filename
                if name.startswith('/') or name.startswith('\\') or '\\' in name or '\x00' in name:
                    raise StudioError('ワールドZIPに危険なパスがあります')
                parts = name.rstrip('/').split('/')
                if any(p in ('', '.', '..') or ':' in p or p[-1:] in (' ', '.') for p in parts):
                    raise StudioError('ワールドZIPに不正なファイル名があります')
                if any(re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', p, re.I) for p in parts):
                    raise StudioError('ワールドZIPにWindowsの予約名があります')
                if entry.flag_bits & 0x1:
                    raise StudioError('暗号化されたワールドZIPは読み込めません')
                if ((entry.external_attr >> 16) & 0o170000) == 0o120000:
                    raise StudioError('シンボリックリンクは許可しません')
                if entry.file_size > 64 * 1024 * 1024 and not name.startswith('db/'):
                    raise StudioError('不自然に大きい非データベースファイルがあります')
                if entry.compress_size > 0 and entry.file_size / entry.compress_size > 800:
                    raise StudioError('不自然に高いZIP圧縮率です')
                if entry.is_dir():
                    continue
                alias = name.casefold()
                if alias in files:
                    raise StudioError('大文字小文字が重複するワールドファイルがあります')
                files[alias] = name
            # Minecraft Education exports normally use root files. Also handle one wrapper directory.
            root = ''
            if 'level.dat' not in files and not any(x.startswith('db/') for x in files):
                candidates = {v.split('/')[0] for v in files.values() if '/' in v}
                if len(candidates) == 1:
                    root = next(iter(candidates)) + '/'
            if (root + 'level.dat').casefold() not in files or not any(x.startswith((root+'db/').casefold()) for x in files):
                raise StudioError('level.datとdb/を含むEducation/Bedrockワールドではありません')
            world_name = '名前未確認'
            namefile = files.get((root + 'levelname.txt').casefold())
            if namefile:
                with z.open(namefile) as f:
                    raw = f.read(1025)
                if len(raw) <= 1024:
                    world_name = raw.decode('utf-8', 'replace').strip()[:120] or '名前未確認'
            # Read the ZIP as a regular file; never search the live game directory.
            sha = hashlib.sha256()
            with source.open('rb') as f:
                for block in iter(lambda: f.read(1024 * 1024), b''):
                    sha.update(block)
            return {'schema': 'mab-studio-mcworld-intake-v1',
                    'path': str(source.resolve()), 'worldName': world_name,
                    'archiveSha256': sha.hexdigest(), 'archiveBytes': source.stat().st_size,
                    'files': len(files), 'root': root,
                    'blockStatus': 'NOT_READ', 'worldWrites': 0,
                    'reason': 'ZIP構造のみ確認。地形ブロックは未解析'}
    except (zipfile.BadZipFile, EOFError) as exc:
        raise StudioError('破損した.mcworld ZIPを検出しました') from exc


def _extract_verified(snapshot: dict, directory: Path) -> Path:
    """Only after inspect; verify unchanged ZIP and safe names before decompression."""
    check = inspect_mcworld(snapshot['path'])
    if check['archiveSha256'] != snapshot['archiveSha256']:
        raise StudioError('エクスポート後にワールドZIPが変更されました')
    with zipfile.ZipFile(snapshot['path']) as z:
        for entry in z.infolist():
            target = directory.joinpath(*entry.filename.rstrip('/').split('/'))
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            # No pathname from ZIP escapes the newly created temp directory.
            with z.open(entry) as src, target.open('xb') as out:
                remaining = entry.file_size
                while remaining > 0:
                    data = src.read(min(1024*1024, remaining))
                    if not data:
                        raise StudioError('ZIPの展開中にデータ不足を検出しました')
                    remaining -= len(data)
                    out.write(data)
    return directory / snapshot['root'].rstrip('/') if snapshot['root'] else directory


def block_color_id(block_id: str) -> int:
    n = block_id.lower()
    if any(s in n for s in ('water', 'bubble_column')): return 0
    if any(s in n for s in ('grass', 'moss', 'leaves', 'vine')): return 1
    if any(s in n for s in ('dirt', 'mud', 'rooted', 'farmland')): return 2
    if any(s in n for s in ('sand', 'sandstone')): return 3
    if any(s in n for s in ('snow', 'ice', 'quartz')): return 4
    if any(s in n for s in ('log', 'plank', 'wood', 'fence')): return 5
    if any(s in n for s in ('glass', 'pane')): return 6
    if any(s in n for s in ('lava', 'magma')): return 7
    return 8


TERRAIN_COLORS = ['#328acb', '#64a560', '#8a7057', '#d0c18d', '#e1edf0',
                  '#9b7d51', '#b8d9ea', '#f39c48', '#92999e', '#f35b61']


def _sample_reader(world, center: tuple[int, int, int], size: int = 24,
                   height: int = 24) -> dict:
    """World-like reader implements has_chunk, get_block. Used with Amulet and mock.

    Missing/failed chunks are counted as unknown, NOT empty terrain.
    """
    if (type(size) is not int or type(height) is not int or not (4 <= size <= 32)
            or not (4 <= height <= 32) or size*size*height > MAX_SAMPLE_VOLUME):
        raise StudioError('地形読み取り範囲が安全上限を超えています')
    if not (isinstance(center, tuple) and len(center) == 3 and all(type(x) is int and abs(x) <= 30_000_000 for x in center)):
        raise StudioError('地形の中心座標を整数XYZで指定してください')
    x0, y0, z0 = (center[0]-size//2, center[1]-height//2, center[2]-size//2)
    cells=[]; unknown=0; known_air=0; bad_chunks=set(); checked={}
    known_air_positions=[]; unknown_positions=[]
    for x in range(x0, x0+size):
        for z in range(z0, z0+size):
            chunk=(x//16,z//16)
            if chunk not in checked:
                try: checked[chunk] = bool(world.has_chunk(*chunk, DIMENSION))
                except Exception: checked[chunk] = False
            if not checked[chunk]:
                unknown += height
                unknown_positions.extend([[x,y,z] for y in range(y0,y0+height)])
                bad_chunks.add(chunk)
                continue
            for y in range(y0, y0+height):
                try:
                    block = world.get_block(x, y, z, DIMENSION)
                    block_id = (str(block.namespace) + ':' + str(block.base_name))
                except Exception:
                    unknown += 1
                    unknown_positions.append([x,y,z])
                    bad_chunks.add(chunk)
                    continue
                if block_id.endswith(':air') or block_id.endswith(':cave_air') or block_id.endswith(':void_air'):
                    known_air += 1
                    known_air_positions.append([x,y,z])
                else:
                    cells.append([x, y, z, block_id])
    return {'schema':'mab-studio-terrain-snapshot-v1', 'center': list(center),
            'origin':[x0, y0, z0], 'size':[size, height, size],
            'dimension':DIMENSION, 'cells':cells,
            'sampled':size*size*height, 'knownAir':known_air, 'unknown':unknown,
            # Full coordinate evidence is bounded by MAX_SAMPLE_VOLUME. No
            # implicit 'everything except solids is air' inference is allowed.
            'knownAirPositions':known_air_positions,
            'unknownPositions':unknown_positions,
            'unreadableChunks':[list(p) for p in sorted(bad_chunks)],
            'complete':unknown==0, 'worldWrites':0, 'source':'EXPORTED_MCWORLD_TEMP_COPY'}


def read_mcworld_terrain(snapshot: dict, center: tuple[int,int,int], size: int = 24,
                         height: int = 24, *, loader=None,
                         planned_anchor: tuple[int,int,int] | None = None) -> dict:
    """Optional Amulet path. No fallback to an invented terrain when unavailable."""
    if loader is None:
        try:
            import amulet
        except ImportError as exc:
            raise StudioError('地形のZIP検査は成功しましたが、ブロック読取用Amuletがありません。地形表示は未実施です') from exc
        loader = amulet.load_level
    with tempfile.TemporaryDirectory(prefix='mab-studio-mcworld-readonly-') as temp:
        path = _extract_verified(snapshot, Path(temp))
        try:
            level = loader(str(path))
            try:
                result = _sample_reader(level, center, size, height)
            finally:
                level.close()  # do NOT call level.save()
        except StudioError:
            raise
        except Exception as exc:
            raise StudioError('Educationのワールド形式を地形として解析できませんでした: ' + str(exc)[:180]) from exc
    anchor = center if planned_anchor is None else planned_anchor
    if not isinstance(anchor, tuple) or len(anchor)!=3 or not all(type(n) is int and abs(n)<=30_000_000 for n in anchor):
        raise StudioError('地形表示の予定配置位置が不正です')
    result['plannedAnchor'] = list(anchor)
    result['archiveSha256'] = snapshot['archiveSha256']
    result['worldName'] = snapshot['worldName']
    return result


def _validate_sample_coverage(terrain: dict, solids: dict) -> tuple[set, set, tuple | None]:
    """Independent bounded snapshot audit: solids, known air, and unreadable
    cells must partition the captured region exactly. Legacy snapshots provide
    no proof of empty cells; they stay UNKNOWN outside reported solid cells.
    """
    spatial = ('origin', 'size', 'sampled', 'knownAir', 'knownAirPositions', 'unknownPositions')
    if not any(k in terrain for k in spatial):
        return set(), set(), None
    if not all(k in terrain for k in spatial):
        raise StudioError('地形のセル単位照合データが不足しています')
    origin, size = terrain['origin'], terrain['size']
    if (not isinstance(origin,list) or len(origin)!=3 or
        not all(type(n) is int and abs(n)<=30_000_000 for n in origin) or
        not isinstance(size,list) or len(size)!=3 or
        not all(type(n) is int and 4<=n<=32 for n in size) or
        size[0]!=size[2] or size[0]*size[1]*size[2]>MAX_SAMPLE_VOLUME):
        raise StudioError('地形サンプルの座標範囲が不正です')
    volume=size[0]*size[1]*size[2]
    if (type(terrain['sampled']) is not int or terrain['sampled']!=volume or
        type(terrain['knownAir']) is not int or terrain['knownAir']<0):
        raise StudioError('地形サンプルのセル総数が不正です')
    def inside(p):
        return all(origin[i]<=p[i]<origin[i]+size[i] for i in range(3))
    def positions(field):
        raw=terrain[field]
        if not isinstance(raw,list) or len(raw)>volume:
            raise StudioError('地形のセル一覧が不正です')
        found=set()
        for p in raw:
            if not isinstance(p,list) or len(p)!=3 or not all(type(v) is int for v in p):
                raise StudioError('地形のセル位置が不正です')
            t=tuple(p)
            if not inside(t) or t in found:
                raise StudioError('地形セルが範囲外または重複しています')
            found.add(t)
        return found
    air=positions('knownAirPositions')
    missing=positions('unknownPositions')
    if (len(air)!=terrain['knownAir'] or len(missing)!=terrain['unknown'] or
        any(not inside(p) for p in solids) or
        (air & missing) or (air & solids.keys()) or (missing & solids.keys()) or
        len(air)+len(missing)+len(solids)!=volume):
        raise StudioError('地形の実ブロック・空気・未読取セル数が矛盾します')
    return air, missing, (tuple(origin),tuple(size))


def create_overlay_scene(terrain: dict, model: dict, anchor: tuple[int,int,int]) -> tuple[dict, dict]:
    """Overlay verified SOURCE with exported terrain, never build/readback.

    Classify every proposed model voxel: known solid overlap, observed air,
    unreadable in sample, or outside sample. In particular, no cell outside the
    export's sampled volume can be treated as air.
    """
    _cells(model)
    if terrain.get('schema') != 'mab-studio-terrain-snapshot-v1' or terrain.get('worldWrites') != 0:
        raise StudioError('地形データの出所が不明です')
    unknown=terrain.get('unknown')
    if type(unknown) is not int or unknown < 0:
        raise StudioError('地形の未読取セル数が不正です')
    if terrain.get('complete') is not (unknown == 0):
        raise StudioError('地形の完全性フラグが一致しません')
    if not isinstance(anchor, tuple) or len(anchor) != 3 or not all(type(x) is int for x in anchor):
        raise StudioError('配置予定位置XYZが不正です')
    captured_anchor = terrain.get('plannedAnchor',terrain.get('center'))
    if captured_anchor is not None and (not isinstance(captured_anchor,list) or
            tuple(captured_anchor) != anchor):
        raise StudioError('地形を読み取った予定位置と現在の予定位置が違います。再度.mcworldを読み取ってください')
    coords = model['bounds']
    shift = (anchor[0] - (coords['minX'] + coords['maxX'])//2,
             anchor[1] - coords['minY'],
             anchor[2] - (coords['minZ'] + coords['maxZ'])//2)
    terrain_map = {}
    raw=terrain.get('cells')
    if not isinstance(raw,list) or len(raw)>MAX_SAMPLE_VOLUME:
        raise StudioError('地形のブロック一覧が不正です')
    for p in raw:
        if not isinstance(p, list) or len(p) != 4 or not all(type(a) is int for a in p[:3]) or not isinstance(p[3], str):
            raise StudioError('地形セルの形式が不正です')
        if tuple(p[:3]) in terrain_map:
            raise StudioError('地形スナップショットが重複しています')
        terrain_map[tuple(p[:3])] = p[3]
    air, missing, box = _validate_sample_coverage(terrain, terrain_map)
    projected = {(p[0]+shift[0], p[1]+shift[1], p[2]+shift[2]): p[3] for p in model['primitives']}
    collision = sum(p in terrain_map for p in projected)
    confirmed_air = sum(p in air for p in projected)
    unreadable = sum(p in missing for p in projected)
    if box:
        origin, size = box
        outside = sum(not all(origin[i]<=p[i]<origin[i]+size[i] for i in range(3)) for p in projected)
        if collision+confirmed_air+unreadable+outside != len(projected):
            raise StudioError('地形照合の分類数が矛盾します')
        unavailable=0
    else:
        # Legacy unpositioned evidence is NOT usable to assert free placement.
        outside=0
        unavailable=len(projected)-collision
    terrain_only = [[*pos, 7 + block_color_id(block), 0]
                    for pos, block in terrain_map.items() if pos not in projected]
    # Material color is retained for observed-air positions. Proposed SOURCE
    # cells that meet observed solids or lack terrain evidence get distinct
    # warning colors. These are still model voxels, never invented world voxels.
    overlap_material = 7 + len(TERRAIN_COLORS)
    unknown_material = overlap_material + 1
    outside_material = overlap_material + 2
    unverified_material = overlap_material + 3
    def model_color(pos, base):
        if pos in terrain_map:
            return overlap_material
        if pos in missing:
            return unknown_material
        if box is not None:
            origin, size = box
            if not all(origin[i] <= pos[i] < origin[i]+size[i] for i in range(3)):
                return outside_material
            return base
        return unverified_material
    model_cells = [[*pos, model_color(pos, mat), 0] for pos, mat in projected.items()]
    all_pts = terrain_only + model_cells
    if not all_pts:
        raise StudioError('表示可能な既知セルがありません')
    cols = list(zip(*(p[:3] for p in all_pts)))
    bounds = dict(zip(('minX','minY','minZ'),map(min, cols))) | dict(zip(('maxX','maxY','maxZ'),map(max, cols)))
    scene = {'primitives': all_pts, 'bounds': bounds,
             'previewPalette': ['#9ba4a7','#e1d8ce','#718391','#6dcce2',
                                '#b78b5b','#ffe18b','#e88777',*TERRAIN_COLORS,
                                '#ef4456','#9e69dc','#f0b84a','#7c8697'],
             'previewLabel':'地形＋設計｜赤:重複 紫:不明 黄:範囲外 灰:旧形式未確認｜未施工',
             'previewOnly':True}
    report = {'archiveSha256':terrain.get('archiveSha256'), 'anchor':list(anchor),
              'visualOnly':True, 'worldWrites':0, 'knownTerrainBlocks':len(terrain_map),
              'unreadableCells':unknown, 'potentialTerrainIntersections':collision,
              'projectedSourceVoxels':len(projected),
              'projectedKnownAir':confirmed_air, 'projectedUnreadable':unreadable,
              'projectedOutsideSample':outside,
              'projectedLegacyUnverified':unavailable,
              'coverageFullyObserved':unreadable==0 and outside==0 and unavailable==0,
              'collisionCertified':False, 'placementAuthorized':False,
              'caution':'重複の有無は静的プレビューのみ。既知の空気も施工許可ではありません。ブロック状態・ワールド同期・当たり判定は未検証'}
    return scene, report
