"""Single-run local Windows acceptance: an actual endpoint request -> B143 -> Tk canvas.

Never talks to Minecraft, never saves a candidate as a user project and never
claims semantic/aesthetic merit from source validity. Never auto-selects or loads
an arbitrary installed model; operator explicitly supplies provider and model.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import time

from app.core import (BASELINE_SHA, EngineManager, ModelTransportError,
                      StudioError, ask_llm, atomic_json, compatibility_check,
                      run_bridge)

DEFAULT_PROMPT = '海辺の小さな天文観測所。地上階の入口、窓、屋上の丸い展望ドームを備えた独創的な建物を設計して。'


def run_acceptance(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description='MAB Studio actual local-model acceptance (no Minecraft calls)')
    parser.add_argument('--acceptance', action='store_true', required=True)
    parser.add_argument('--provider', required=True, choices=('Ollama', 'LM Studio'))
    parser.add_argument('--model', required=True)
    parser.add_argument('--prompt', default=DEFAULT_PROMPT)
    parser.add_argument('--report', required=True)
    parser.add_argument('--engine', default=None,
                        help='Optional preverified engine directory for CI; never an unreviewed engine')
    ns = parser.parse_args(argv)
    report_path = Path(ns.report).expanduser().resolve()
    report = {
        'schema': 'mab-studio-local-ai-gui-acceptance-v1',
        'createdAt': datetime.now(timezone.utc).isoformat(),
        'outcome': 'FAIL',
        'stage': 'PREFLIGHT',
        'provider': ns.provider,
        'model': ns.model[:180],
        'promptSha256': hashlib.sha256(ns.prompt.encode('utf-8')).hexdigest(),
        'sourceValid': False,
        'guiRendered': False,
        'actualProviderRequestAttempted': False,
        'providerIdentityIndependentlyVerified': False,
        'architecturalIntentVerified': False,
        'humanVisualQualityVerified': False,
        'minecraftEducationTested': False,
        'constructionApproved': False,
        'worldContacts': 0,
        'worldWrites': 0,
        'BuildPlan': {'operations': []},
        'details': 'SOURCE/GUI smoke is not proof of aesthetics, Japanese-intent fidelity, or in-world construction',
    }
    window = None
    started = time.monotonic()
    try:
        if not ns.model.strip() or not ns.prompt.strip() or len(ns.prompt) > 2000:
            raise StudioError('受入テストのモデル名または日本語指示が無効です')
        report['stage'] = 'PINNED_ENGINE'
        if ns.engine:
            engine = Path(ns.engine).resolve()
            report['engineInstallation'] = 'EXISTING_EXPLICIT_PINNED_PATH'
        else:
            manager = EngineManager()
            engine = manager.active_path
            if engine is None:
                manager.install(BASELINE_SHA)
                engine = manager.active_path
            if engine is None:
                raise StudioError('安全確認済みB143エンジンを導入できません')
            report['engineInstallation'] = 'EXISTING_OR_NEWLY_PINNED_MODULE'
        receipt = compatibility_check(engine)
        report['engine'] = {'commit': BASELINE_SHA, 'smokeGeometrySha256': receipt['geometrySha256']}
        report['stage'] = 'LOCAL_MODEL_REQUEST'
        # Exactly ONE explicit inference for diagnostic predictability. Retry is
        # separately available in ordinary GUI design, not hidden in this test.
        report['actualProviderRequestAttempted'] = True
        llm_started = time.monotonic()
        design = ask_llm(ns.provider, ns.model, ns.prompt)
        report['providerRequestSeconds'] = round(time.monotonic() - llm_started, 2)
        report['stage'] = 'REAL_MAB_SOURCE_VERIFICATION'
        scene = run_bridge(engine, design)
        if scene['stats']['voxels'] < 1:
            raise StudioError('MABが空の設計を返しました')
        report['sourceValid'] = True
        report['source'] = {
            'sourceSha256': scene['sourceSha256'],
            'geometrySha256': scene['geometrySha256'],
            'voxelCount': scene['stats']['voxels'],
            'readiness': scene['readiness'],
        }
        report['stage'] = 'TK_SOURCE_PREVIEW'
        # Uses the same visualization component and actual compiled primitives as
        # the Windows GUI; does not create a project, import a world, or write JSON
        # containing model output. Hidden canvas is still actively rendered.
        import tkinter as tk
        from app.preview import VoxelPreview
        window = tk.Tk()
        window.title('MAB Studio source-only acceptance')
        window.geometry('900x630')
        preview = VoxelPreview(window, width=860, height=580)
        preview.pack(fill='both', expand=True)
        window.update_idletasks()
        preview.set_detail('全体メッシュ（境界面）')
        preview.show(scene)
        window.update_idletasks()
        items = len(preview.find_all())
        if items < 4:
            raise StudioError('設計SOURCEの3D描画が完了しませんでした')
        report['guiRendered'] = True
        report['guiCanvasItems'] = items
        report['previewMode'] = 'GREEDY_SURFACE_MESH'
        report['outcome'] = 'PASS_SOURCE_AND_GUI_ONLY'
        report['stage'] = 'COMPLETE'
    except Exception as exc:
        # Do not write the sensitive model response or prompt to disk.
        report['failureClass'] = type(exc).__name__
        report['error'] = str(exc)[:450]
        report['outcome'] = 'FAIL'
    finally:
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass
        report['totalSeconds'] = round(time.monotonic() - started, 2)
        try:
            atomic_json(report_path, report)
        except (OSError, ValueError):
            return 2
    return 0 if report['outcome'] == 'PASS_SOURCE_AND_GUI_ONLY' else 1
