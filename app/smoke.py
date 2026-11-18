"""Explicit packaged-GUI startup smoke. Never opens a Minecraft connection."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile
import traceback
import sys

from app.core import atomic_json, compatibility_check, node_binary, run_bridge
from app.examples import example_library
from app.release_runtime import verify_packaged_runtime


def run_smoke(studio_class, args: list[str]) -> int:
    parser = argparse.ArgumentParser(description='MAB Studio isolated GUI smoke')
    parser.add_argument('--smoke', action='store_true', required=True)
    parser.add_argument('--smoke-report', required=True)
    parser.add_argument('--smoke-engine', required=True)
    ns = parser.parse_args(args)
    report_path = Path(ns.smoke_report).resolve()
    report = {'schema': 'mab-studio-windows-gui-smoke-v1',
              'result': 'FAIL', 'worldContacts': 0, 'worldWrites': 0}
    old_appdata = os.environ.get('LOCALAPPDATA')
    root = None
    try:
        with tempfile.TemporaryDirectory(prefix='mabstudio-release-smoke-') as d:
            os.environ['LOCALAPPDATA'] = d
            engine = Path(ns.smoke_engine).resolve()
            report['bundle'] = verify_packaged_runtime()
            report['nodeBinary'] = str(node_binary())
            report['engineCompatibility'] = compatibility_check(engine)
            # Actual verified engine, actual source compiler, actual Tk Canvas.
            scene = run_bridge(engine, example_library())
            root = studio_class()
            root.update_idletasks()
            # A packaged Tk process that exits before mainloop needs one real
            # event pump so Windows can map the window and deliver Configure.
            # update_idletasks alone leaves the native viewer at 1x1 on some
            # Windows desktops, causing its first VTK frame to be skipped.
            pump = getattr(root, 'update', None)
            if callable(pump):
                pump()
            root.preview.show(scene)
            root.update_idletasks()
            from app.native_viewer import NativeVoxelPreview
            if scene['stats']['voxels'] != 917:
                raise RuntimeError('VERIFIED_SOURCE_VOXEL_COUNT_MISMATCH')
            if isinstance(root.preview, NativeVoxelPreview):
                # The frozen EXE defaults to native VTK, not Tk Canvas. Checking
                # find_all() here wrongly rejects the actual release renderer.
                # Verify the displayed embedded viewer, not only a standalone
                # synthetic framebuffer smoke or a lightweight fallback.
                from app.native_selfcheck import _image_signature
                root.preview.draw()
                if root.preview._visible_count != 917 or root.preview._photo is None:
                    raise RuntimeError('PACKAGED_NATIVE_SOURCE_SCENE_NOT_VISIBLE')
                pixels = _image_signature(root.preview)
                report.update(guiRenderer='VTK_NATIVE_EMBEDDED',
                              nativeScenePixelsVerified=True,
                              nativeFramebuffer=list(root.preview._rendered_size),
                              nativePixelSignatureSha256=pixels,
                              nativeCells=root.preview._visible_count)
            else:
                # Only a source developer run may accept the lightweight view.
                # A packaged Windows release is required to render B143 inside VTK.
                if getattr(sys, 'frozen', False):
                    raise RuntimeError('PACKAGED_NATIVE_VIEWER_NOT_ACTIVE')
                shapes = len(root.preview.find_all())
                if shapes < 10:
                    raise RuntimeError(f'Incomplete lightweight GUI preview: {shapes} canvas items')
                report.update(guiRenderer='TK_LIGHTWEIGHT_SOURCE_ONLY',
                              nativeScenePixelsVerified=False,
                              canvasItems=shapes)
            report.update(result='PASS', sourceVoxels=917,
                          geometrySha256=scene['geometrySha256'])
    except Exception as exc:
        report['error'] = str(exc)[:500]
        report['traceback'] = traceback.format_exc()[-3000:]
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass
        if old_appdata is None:
            os.environ.pop('LOCALAPPDATA', None)
        else:
            os.environ['LOCALAPPDATA'] = old_appdata
        atomic_json(report_path, report)
    return 0 if report['result'] == 'PASS' else 1
