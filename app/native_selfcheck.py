"""Bounded read-only first-run QA for the optional embedded VTK renderer.

No local AI call, MAB engine call, Education network connection or world read/write.
The synthetic fixture only exists in memory. Used by the one-click launcher and
Windows release QA; it must not be represented as actual user-model acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path


def _image_signature(widget) -> str:
    """Hash sparse *rendered* framebuffer pixels, not the existence of a Tk image.

    A frozen EXE can initialize VTK and create a PhotoImage while its GPU
    displays only a constant blank frame. Palette-change comparisons should
    catch that false positive, without exporting screenshots or SOURCE data.
    """
    from vtkmodules.util.numpy_support import vtk_to_numpy
    w, h = widget._rendered_size
    if w < 100 or h < 100 or widget._photo is None:
        raise RuntimeError('NATIVE_3D_FRAMEBUFFER_NOT_PRESENT')
    scalars = widget._capture.GetOutput().GetPointData().GetScalars()
    if scalars is None or scalars.GetNumberOfTuples() != w*h:
        raise RuntimeError('NATIVE_3D_FRAMEBUFFER_PIXELS_MISSING')
    pixels = vtk_to_numpy(scalars).reshape(h, w, 3)
    sample = pixels[::max(1, h//64), ::max(1, w//64), :]
    return hashlib.sha256(sample.tobytes()).hexdigest()


def _extended_scene() -> dict:
    """Exactly 3,128 synthetic SOURCE-shaped cells; never user data/world data."""
    voxels = [[x, y, z, (x+2*y+3*z) % 11] for x in range(16)
              for y in range(12) for z in range(16)]
    voxels += [[x, 12, z, (x+z) % 11] for x in range(4, 12)
               for z in range(4, 11)]
    return {'primitives': voxels,
            'previewPalette': ['#b9a28b','#d4d9df','#5689a7','#d1a56e',
                               '#6a9b8c','#d7c37d','#b47673','#67798d',
                               '#b2b6bf','#806f9e','#72875e']}


def inspect_native_viewer(*, extended: bool = False) -> dict:
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
    root.geometry('720x540+-2100+-2100' if extended else '520x400+-2100+-2100')
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
        first_image = _image_signature(widget)
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
        # Change only colors, not geometry, camera or framebuffer size.
        # The pixels must change; a blank/unchanging EXE cannot claim render PASS.
        altered = {'primitives': voxels, 'previewPalette': ['#d36b49','#f4d85d']}
        widget.show(altered)
        widget.draw()
        if _image_signature(widget) == first_image:
            raise RuntimeError('NATIVE_3D_FRAMEBUFFER_UNCHANGED_AFTER_PALETTE')
        stress = None
        if extended:
            large = _extended_scene()
            widget.show(large)
            widget.draw()
            if widget._visible_count != 3128:
                raise RuntimeError('NATIVE_3D_EXTENDED_SCENE_COUNT')
            all_image = _image_signature(widget)
            camera_images = set()
            for direction in ('正面', '右側', '上面', '斜め'):
                widget.set_view(direction)
                camera_images.add(_image_signature(widget))
            if len(camera_images) < 2:
                raise RuntimeError('NATIVE_3D_EXTENDED_CAMERA_UNCHANGED')
            widget.set_cut_level(4)
            widget.draw()
            if widget._visible_count != 1280 or _image_signature(widget) == all_image:
                raise RuntimeError('NATIVE_3D_EXTENDED_SECTION_PIXELS')
            widget.set_cut_level(None)
            if widget._visible_count != 3128:
                raise RuntimeError('NATIVE_3D_EXTENDED_SECTION_RESTORE')
            # A packaged EXE must preserve interaction quality: a drag frame
            # may use fewer pixels, but the actual Tk image remains full-size
            # and a crisp VTK framebuffer is restored on release.
            from types import SimpleNamespace
            full_width, full_height = widget._rendered_size
            widget._start_rotate(SimpleNamespace(x=100, y=80, state=0))
            widget._drag_to(SimpleNamespace(x=145, y=110))
            widget.draw()
            drag_width, drag_height = widget._rendered_size
            if (drag_width >= full_width or drag_height >= full_height or
                    widget._photo_size != (full_width, full_height) or
                    not widget._interactive_preview):
                raise RuntimeError('NATIVE_3D_DRAG_PREVIEW_NOT_REDUCED')
            widget._end_drag()
            widget.draw()
            if (widget._rendered_size != (full_width, full_height) or
                    widget._photo_size != (full_width, full_height) or
                    widget._interactive_preview):
                raise RuntimeError('NATIVE_3D_FULL_QUALITY_NOT_RESTORED')
            stress = {'cells': 3128, 'paletteEntries': 11,
                      'views': 4, 'sectionCells': 1280,
                      'pixelChangeVerified': True,
                      'cameraPixelChangeVerified': True,
                      'dragPreviewVerified': True,
                      'fullResolutionRestored': True,
                      'dragFramebuffer': [drag_width, drag_height],
                      'renderTimings': widget.render_statistics()}
        return {'result': 'PASS_NATIVE_READONLY_SELF_CHECK',
                'renderer': 'VTK_OFFSCREEN_TK', 'source': 'SYNTHETIC_IN_MEMORY',
                'cells': 64, 'views': 4, 'framebuffer': list(size),
                'paletteChangePixelsVerified': True, 'extendedScene': stress,
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
    p.add_argument('--extended', action='store_true', help='Render 3,128 synthetic cells as well')
    args = p.parse_args(argv)
    try:
        result = inspect_native_viewer(extended=args.extended)
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
