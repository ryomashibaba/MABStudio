"""Read-only proof that a saved Studio revision can render in native VTK.

This does not recompile B143, send a model request, inspect a Minecraft world,
change the active project, or approve construction. The immutable saved snapshot
is checked by RevisionStore and the actual VTK framebuffer is exercised.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tkinter as tk

from app.core import user_root
from app.revisions import RevisionStore, HEX32, _cells
from app.gpu_viewer import compact_display_data
from app.native_selfcheck import _image_signature


def _sha_file(path: Path) -> str:
    """Observe bytes without creating/mutating a revision or index."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_saved_revision(project_root: Path, revision_id: str) -> dict:
    if not isinstance(revision_id, str) or not HEX32.fullmatch(revision_id):
        raise ValueError('INVALID_SAVED_REVISION_ID')
    project_root = Path(project_root).resolve()
    snapshot_path = project_root / 'revisions' / (revision_id + '.json')
    index_path = project_root / 'project.json'
    before = _sha_file(snapshot_path)
    index_before = _sha_file(index_path) if index_path.is_file() else None

    snapshot = RevisionStore(project_root).read_revision(revision_id)
    scene = snapshot['scene']
    source_cells = _cells(scene)
    displayed = compact_display_data(scene)
    if len(displayed['voxels']) != len(source_cells):
        raise ValueError('SAVED_SOURCE_DISPLAY_COUNT_MISMATCH')
    from app.release_runtime import verify_packaged_runtime
    runtime = verify_packaged_runtime()
    from app.native_viewer import NativeVoxelPreview, native_dependencies_available
    if not native_dependencies_available():
        raise RuntimeError('SAVED_NATIVE_VTK_UNAVAILABLE')
    root = tk.Tk()
    root.title('MAB Studio saved SOURCE native read-only check')
    root.geometry('780x600+-2100+-2100')
    try:
        preview = NativeVoxelPreview(root)
        preview.pack(fill='both', expand=True)
        root.update()
        preview.show(scene)
        preview.draw()
        if preview._photo is None or preview._visible_count != len(source_cells):
            raise RuntimeError('SAVED_NATIVE_CELL_RENDER_FAILED')
        if min(preview._rendered_size) < 100:
            raise RuntimeError('SAVED_NATIVE_FRAMEBUFFER_TOO_SMALL')
        views = {}
        for name in ('斜め', '正面', '右側', '上面'):
            preview.set_view(name)
            preview.draw()
            views[name] = _image_signature(preview)
        if len(set(views.values())) < 2:
            raise RuntimeError('SAVED_NATIVE_CAMERA_PIXELS_UNCHANGED')
        heights = [v[1] for v in displayed['voxels']]
        cut_checked = min(heights) < max(heights)
        cut_cells = None
        if cut_checked:
            level = min(heights) + (max(heights) - min(heights)) // 2
            expected = sum(y <= level for y in heights)
            preview.set_cut_level(level)
            preview.draw()
            if preview._visible_count != expected:
                raise RuntimeError('SAVED_NATIVE_CUT_CELL_COUNT_MISMATCH')
            cut_cells = expected
            preview.set_cut_level(None)
            preview.draw()
            if preview._visible_count != len(source_cells):
                raise RuntimeError('SAVED_NATIVE_CUT_RESTORE_FAILED')
        timings = preview.render_statistics()
        viewport = list(preview._rendered_size)
    finally:
        root.destroy()

    if _sha_file(snapshot_path) != before or (index_before is not None and _sha_file(index_path) != index_before):
        raise RuntimeError('SAVED_REVISION_FILE_CHANGED_DURING_READ_ONLY_CHECK')
    return {
        'schema': 'mab-studio-saved-native-readonly-proof-v1',
        'result': 'PASS_SAVED_REVISION_NATIVE_RENDER_ONLY',
        'source': 'SAVED_IMMUTABLE_REVISION',
        'revisionId': revision_id,
        'projectId': snapshot['projectId'],
        'sourceSha256': snapshot['sourceSha256'],
        'geometrySha256': snapshot['geometrySha256'],
        'savedRevisionIntegrityVerified': True,
        'revisionAndIndexUnchanged': True,
        'cells': len(source_cells),
        'views': len(views),
        'cameraPixelChangeVerified': True,
        'sectionChecked': cut_checked,
        'sectionCells': cut_cells,
        'framebuffer': viewport,
        'renderTimings': timings,
        'packagedRuntimeVerified': runtime['packagedRuntimeVerified'],
        'runtimeMode': runtime['mode'],
        'sourceRecompiledWithB143': False,
        'userVisualQualityApproved': False,
        'hardwareGpuIndependentlyVerified': False,
        'modelRequests': 0,
        'minecraftWorldContacts': 0,
        'worldWrites': 0,
        'constructionApproved': False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Read-only VTK check of an immutable Studio revision')
    parser.add_argument('--revision-id', required=True)
    parser.add_argument('--project-root', type=Path, default=user_root() / 'projects')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args(argv)
    report_outside_project = (args.report is None or
                              not args.report.resolve().is_relative_to(args.project_root.resolve()))
    try:
        # The diagnostics output must never be written into the project store.
        if not report_outside_project:
            raise ValueError('REPORT_MUST_BE_OUTSIDE_SAVED_PROJECT_STORE')
        result = inspect_saved_revision(args.project_root, args.revision_id)
        code = 0
    except Exception as exc:
        result = {'schema': 'mab-studio-saved-native-readonly-proof-v1',
                  'result': 'FAIL_SAVED_REVISION_NATIVE_RENDER',
                  'error': type(exc).__name__, 'reason': str(exc)[:240],
                  'sourceRecompiledWithB143': False, 'modelRequests': 0,
                  'minecraftWorldContacts': 0, 'worldWrites': 0,
                  'constructionApproved': False}
        code = 1
    if args.report and report_outside_project:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if sys.stdout is not None:
        print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == '__main__':
    sys.exit(main())
