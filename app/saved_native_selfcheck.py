"""Read-only proof that a saved Studio revision can render in native VTK.

This does not recompile B143, send a model request, inspect a Minecraft world,
change the active project, or approve construction. The immutable saved snapshot
is checked by RevisionStore and the actual VTK framebuffer is exercised.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tkinter as tk
import time

from app.core import user_root
from app.revisions import RevisionStore, HEX32, _cells
from app.gpu_viewer import compact_display_data
from app.native_selfcheck import _image_signature


def _sha_file(path: Path) -> str:
    """Observe bytes without creating/mutating a revision or index."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _working_set_mb() -> float | None:
    """Read this QA process' resident memory on Windows without extra packages."""
    if os.name != 'nt':
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                        ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                        ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                        ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                        ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        handle = kernel32.GetCurrentProcess()
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return round(counters.WorkingSetSize / (1024 * 1024), 1)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _timing_summary(wall: list[float], frames: list[float], restores: list[float] | None = None) -> dict:
    def percentile(values: list[float], p: float):
        ordered = sorted(values)
        if not ordered:
            return None
        return round(ordered[min(len(ordered)-1, math.ceil(len(ordered)*p)-1)], 2)
    result = {'samples': len(wall), 'wallMedianMs': percentile(wall, .5),
              'wallP95Ms': percentile(wall, .95), 'renderMedianMs': percentile(frames, .5),
              'renderP95Ms': percentile(frames, .95)}
    if restores is not None:
        result['restoreMedianMs'] = percentile(restores, .5)
        result['restoreP95Ms'] = percentile(restores, .95)
    return result


def inspect_saved_revision(project_root: Path, revision_id: str) -> dict:
    if not isinstance(revision_id, str) or not HEX32.fullmatch(revision_id):
        raise ValueError('INVALID_SAVED_REVISION_ID')
    project_root = Path(project_root).resolve()
    snapshot_path = project_root / 'revisions' / (revision_id + '.json')
    index_path = project_root / 'project.json'
    before = _sha_file(snapshot_path)
    index_before = _sha_file(index_path) if index_path.is_file() else None
    memory_before = _working_set_mb()

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
        level = min(heights) + (max(heights) - min(heights)) // 2 if cut_checked else None
        if cut_checked:
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
        backend = preview.renderer_diagnostics()
        views_order = ('正面', '背面', '左側', '右側', '上面', '斜め')
        measurements = {'cameraRotation': ([], [], []), 'zoom': ([], [], None),
                        'pan': ([], [], []), 'sectionToggle': ([], [], [])}
        pan_pixel_change_verified = False
        from types import SimpleNamespace
        for index in range(12):
            wall, frames, restores = measurements['cameraRotation']
            tick = time.perf_counter()
            preview._start_rotate(SimpleNamespace(x=100, y=80, state=0))
            preview._drag_to(SimpleNamespace(x=120 + index, y=90 + index))
            preview.draw(); frames.append(preview._last_frame_ms.get('total', 0.0))
            preview._end_drag(); preview.draw(); restores.append(preview._last_frame_ms.get('total', 0.0))
            wall.append((time.perf_counter()-tick)*1000)
            preview.set_view(views_order[index % len(views_order)])

            wall, frames, _ = measurements['zoom']
            tick = time.perf_counter()
            preview._zoom_at(1.04, (preview._photo_size[0]//2, preview._photo_size[1]//2))
            preview.draw(); frames.append(preview._last_frame_ms.get('total', 0.0))
            wall.append((time.perf_counter()-tick)*1000)

            wall, frames, restores = measurements['pan']
            tick = time.perf_counter()
            pan_before = _image_signature(preview) if index == 0 else None
            preview._start_pan(SimpleNamespace(x=100, y=80, state=0))
            preview._drag_to(SimpleNamespace(x=111 + index, y=91 + index))
            preview.draw(); frames.append(preview._last_frame_ms.get('total', 0.0))
            if index == 0:
                pan_pixel_change_verified = _image_signature(preview) != pan_before
                if not pan_pixel_change_verified:
                    raise RuntimeError('SAVED_NATIVE_PAN_PIXELS_UNCHANGED')
            preview._end_drag(); preview.draw(); restores.append(preview._last_frame_ms.get('total', 0.0))
            wall.append((time.perf_counter()-tick)*1000)

            if cut_checked:
                wall, frames, restores = measurements['sectionToggle']
                tick = time.perf_counter()
                preview.set_cut_level(level); preview.draw()
                frames.append(preview._last_frame_ms.get('total', 0.0))
                preview.set_cut_level(None); preview.draw()
                restores.append(preview._last_frame_ms.get('total', 0.0))
                wall.append((time.perf_counter()-tick)*1000)
        per_action = {name: _timing_summary(*values) for name, values in measurements.items()}
        idle_samples = len(preview._frame_ms)
        root.after(120, root.quit)
        root.mainloop()
        idle_render_frames = len(preview._frame_ms) - idle_samples
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
        'interactionTimingsMs': per_action,
        'panPixelChangeVerified': pan_pixel_change_verified,
        'idle': {'observationMs': 120, 'renderFrames': idle_render_frames},
        'workingSetMb': {'beforeScene': memory_before, 'afterInteractions': _working_set_mb()},
        'rendererDiagnostics': backend,
        'hardwareGpuIndependentlyVerified': backend['classification'] == 'PHYSICAL_GPU_RENDERER',
        'packagedRuntimeVerified': runtime['packagedRuntimeVerified'],
        'runtimeMode': runtime['mode'],
        'sourceRecompiledWithB143': False,
        'userVisualQualityApproved': False,
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
