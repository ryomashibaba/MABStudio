"""Offline WebGL scene viewer for SOURCE-derived, read-only voxel geometry.

Creates a private standalone HTML snapshot. No HTTP server, CDN, external JS,
Minecraft connection, building edits, subprocess, or model inference occurs.
The browser uses its own GPU/WebGL implementation; Tk Canvas remains fallback.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import uuid
import webbrowser


class GPUViewerError(ValueError):
    pass


MAX_VOXELS = 200_000
RGB = re.compile(r'^#[0-9a-fA-F]{6}$')
DEFAULT_PALETTE = ['#9ba4a7', '#e1d8ce', '#718391', '#6dcce2', '#b78b5b', '#ffe18b', '#e88777']


def _assets() -> Path:
    if getattr(sys, 'frozen', False):
        bundle = getattr(sys, '_MEIPASS', None)
        if not bundle:
            raise GPUViewerError('GPU_VIEWER_FROZEN_ASSETS_MISSING')
        return Path(bundle) / 'app' / 'gpu_viewer_assets'
    return Path(__file__).resolve().parent / 'gpu_viewer_assets'


def compact_display_data(scene: dict, cut_level: int | None = None) -> dict:
    """Filter strictly to inert view data; never serialize world / AI credentials."""
    if not isinstance(scene, dict):
        raise GPUViewerError('GPU_VIEWER_SCENE_MISSING')
    points = scene.get('primitives')
    if not isinstance(points, list) or not points:
        raise GPUViewerError('GPU_VIEWER_GEOMETRY_MISSING')
    if len(points) > MAX_VOXELS:
        raise GPUViewerError(f'GPU_VIEWER_LIMIT_{MAX_VOXELS}_VOXELS')
    palette = scene.get('previewPalette', DEFAULT_PALETTE)
    if not isinstance(palette, list) or not palette or len(palette) > 256:
        raise GPUViewerError('GPU_VIEWER_PALETTE_INVALID')
    if any(not isinstance(c, str) or not RGB.fullmatch(c) for c in palette):
        raise GPUViewerError('GPU_VIEWER_PALETTE_INVALID')
    voxels = []
    seen = set()
    for p in points:
        if not isinstance(p, (tuple, list)) or len(p) < 4:
            raise GPUViewerError('GPU_VIEWER_CELL_INVALID')
        x, y, z, mat = p[:4]
        if any(type(v) is not int or abs(v) > 1_000_000 for v in (x, y, z)):
            raise GPUViewerError('GPU_VIEWER_COORDINATE_INVALID')
        if type(mat) is not int or mat < 0 or mat >= 256:
            raise GPUViewerError('GPU_VIEWER_MATERIAL_INVALID')
        xyz = (x, y, z)
        if xyz in seen:
            raise GPUViewerError('GPU_VIEWER_DUPLICATE_CELL')
        seen.add(xyz)
        voxels.append([x, y, z, mat])
    bounds = {'minX': min(v[0] for v in voxels), 'maxX': max(v[0] for v in voxels),
              'minY': min(v[1] for v in voxels), 'maxY': max(v[1] for v in voxels),
              'minZ': min(v[2] for v in voxels), 'maxZ': max(v[2] for v in voxels)}
    if cut_level is not None and (type(cut_level) is not int or not (bounds['minY']-1 <= cut_level <= bounds['maxY'])):
        raise GPUViewerError('GPU_VIEWER_SECTION_INVALID')
    label = scene.get('previewLabel')
    if not isinstance(label, str):
        label = '検証済み建築'
    return {'schema': 'mab-studio-offline-gpu-preview-v1',
            'voxels': voxels,
            'palette': [c.lower() for c in palette],
            'bounds': bounds,
            'title': label[:140],
            'sectionLevel': cut_level}


def render_offline_html(scene: dict, *, cut_level: int | None = None) -> str:
    data = compact_display_data(scene, cut_level)
    asset_root = _assets()
    template_path = asset_root / 'viewer.html'
    script_path = asset_root / 'viewer.js'
    if not template_path.is_file() or not script_path.is_file():
        raise GPUViewerError('GPU_VIEWER_ASSETS_MISSING')
    template = template_path.read_text(encoding='utf-8')
    js = script_path.read_text(encoding='utf-8')
    if template.count('__MAB_SCENE_JSON__') != 1 or template.count('__MAB_VIEWER_JS__') != 1:
        raise GPUViewerError('GPU_VIEWER_TEMPLATE_INVALID')
    # Escaping prevents literal `</script>` and HTML interpretation in the
    # serialized label; the on-page renderer uses textContent only.
    payload = json.dumps(data, ensure_ascii=True, separators=(',', ':'))
    payload = payload.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    return template.replace('__MAB_SCENE_JSON__', payload).replace('__MAB_VIEWER_JS__', js)


def create_offline_preview(scene: dict, root: Path, *, cut_level: int | None = None) -> Path:
    """Save a NEW user-private HTML; no overwrite of any saved project."""
    content = render_offline_html(scene, cut_level=cut_level)
    folder = root / 'gpu-preview'
    folder.mkdir(parents=True, exist_ok=True)
    result = folder / ('viewer-' + uuid.uuid4().hex + '.html')
    descriptor = os.open(result, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write(content)
    return result


def open_offline_preview(scene: dict, root: Path, *, cut_level: int | None = None) -> Path:
    path = create_offline_preview(scene, root, cut_level=cut_level)
    if not webbrowser.open(path.resolve().as_uri(), new=1):
        raise GPUViewerError('ブラウザーを開けませんでした。HTMLは次の場所に保存されています: ' + str(path))
    return path
