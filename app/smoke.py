"""Explicit packaged-GUI startup smoke. Never opens a Minecraft connection."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile
import traceback

from app.core import atomic_json, compatibility_check, node_binary, run_bridge
from app.examples import example_library


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
            report['nodeBinary'] = str(node_binary())
            report['engineCompatibility'] = compatibility_check(engine)
            # Actual verified engine, actual source compiler, actual Tk Canvas.
            scene = run_bridge(engine, example_library())
            root = studio_class()
            root.update_idletasks()
            root.preview.show(scene)
            root.update_idletasks()
            shapes = len(root.preview.find_all())
            if scene['stats']['voxels'] != 917 or shapes < 10:
                raise RuntimeError(f'Incomplete verified GUI preview: {shapes} canvas items')
            report.update(result='PASS', sourceVoxels=917,
                          geometrySha256=scene['geometrySha256'],
                          canvasItems=shapes, previewDetail=root.preview.detail_limit)
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
