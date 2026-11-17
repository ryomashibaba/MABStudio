"""Bounded read-only first-run QA for the optional embedded VTK renderer.

No local AI call, MAB engine call, Education network connection or world read/write.
The synthetic fixture only exists in memory. Used by the one-click launcher and
Windows release QA; it must not be represented as actual user-model acceptance.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def inspect_native_viewer() -> dict:
    import tkinter as tk
    from app.release_runtime import verify_packaged_runtime
    # A packaged EXE must pass its private runtime audit before 3D is certified.
    runtime = verify_packaged_runtime()
    from app.native_viewer import NativeVoxelPreview, native_dependencies_available

    if not native_dependencies_available():
        raise RuntimeError('VTK_OR_PILLOW_NOT_AVAILABLE')
    root = tk.Tk()
    root.title('MAB Studio native 3D self-check')
    # VTK uses its own offscreen OpenGL context. An offscreen-positioned Tk
    # window provides actual widget dimensions without disturbing the desktop.
    root.geometry('520x400+-2100+-2100')
    widget = None
    try:
        widget = NativeVoxelPreview(root)
        widget.pack(fill='both', expand=True)
        root.update()
        # 4x3x5 solid structure plus 2x2 roof, nonuniform and two materials.
        voxels = [[x, y, z, 0] for x in range(4) for y in range(3) for z in range(5)]
        voxels += [[x, 3, z, 1] for x in range(1, 3) for z in range(1, 3)]
        scene = {'primitives': voxels, 'previewPalette': ['#b8c8d8', '#6bd6ef']}
        started = time.monotonic()
        widget.show(scene)
        widget.draw()
        if widget._visible_count != 64 or widget._photo is None:
            raise RuntimeError('NATIVE_3D_RENDER_NOT_VISIBLE')
        size = widget._rendered_size
        if size[0] < 100 or size[1] < 100:
            raise RuntimeError('NATIVE_3D_FRAMEBUFFER_TOO_SMALL')
        for name in ('正面', '上面', '右側', '斜め'):
            widget.set_view(name)
            if widget._photo is None:
                raise RuntimeError('NATIVE_3D_CAMERA_RENDER_FAILED')
        widget.set_cut_level(1)
        if widget._visible_count != 40:
            raise RuntimeError('NATIVE_3D_SECTION_FAILED')
        widget.set_cut_level(None)
        if widget._visible_count != 64:
            raise RuntimeError('NATIVE_3D_SECTION_RESTORE_FAILED')
        return {'result': 'PASS_NATIVE_READONLY_SELF_CHECK',
                'renderer': 'VTK_OFFSCREEN_TK', 'source': 'SYNTHETIC_IN_MEMORY',
                'cells': 64, 'views': 4, 'framebuffer': list(size),
                'elapsedSeconds': round(time.monotonic()-started, 3),
                'packagedRuntimeVerified': runtime['packagedRuntimeVerified'],
                'runtimeMode': runtime['mode'],
                'renderTimings': widget.render_statistics(),
                'localAiCalls': 0, 'worldContacts': 0, 'worldWrites': 0}
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description='Read-only embedded native 3D smoke')
    p.add_argument('--report', type=Path)
    args = p.parse_args(argv)
    try:
        result = inspect_native_viewer()
        status = 0
    except Exception as exc:
        result = {'result': 'FAIL_NATIVE_READONLY_SELF_CHECK',
                  'error': type(exc).__name__, 'reason': str(exc)[:240],
                  'packagedRuntimeVerified': False,
                  'worldContacts': 0, 'worldWrites': 0}
        status = 1
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    return status


if __name__ == '__main__':
    sys.exit(main())
