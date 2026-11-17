"""Single-run local Windows acceptance: an actual endpoint request -> B143 -> Tk canvas.

Never talks to Minecraft and never claims semantic/aesthetic merit from source
validity. Successful real-model results may be saved as a NEW, source-only Studio
project only with --save-to-studio; the existing history is never deleted.
Never auto-selects or loads an arbitrary installed model; operator explicitly
supplies provider and model.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import time
import sys

from app.release_runtime import verify_packaged_runtime
from app.core import (BASELINE_SHA, EngineManager, ModelTransportError,
                      StudioError, ask_llm, atomic_json, compatibility_check,
                      run_bridge, MODEL_GENERATION_SCHEMA)

DEFAULT_PROMPT = '海辺の小さな天文観測所。地上階の入口、窓、屋上の丸い展望ドームを備えた独創的な建物を設計して。'


def inspect_candidate_shape_fields(design: dict) -> dict:
    """Privacy-preserving *syntactic hints*, never B143 validation/repair.

    The original model answer is not persisted.  B143 remains the authority
    for exact shapes, dimensions, support, materials, complexity and safety.
    """
    items = design.get('elements') if isinstance(design, dict) else None
    if not isinstance(items, list):
        return {'elementCount': None, 'hints': [{'kind': 'ELEMENTS_NOT_ARRAY'}],
                'modelContentSaved': False}
    hints = []
    arms = MODEL_GENERATION_SCHEMA['properties']['elements']['items']['anyOf']
    for index, element in enumerate(items[:160]):
        if not isinstance(element, dict):
            hints.append({'index': index, 'kind': 'ELEMENT_NOT_OBJECT'})
            continue
        variant = next((arm for arm in arms
                        if arm['properties']['shape']['const'] == element.get('shape')
                        and arm['properties']['mode']['const'] == element.get('mode')), None)
        if variant is None:
            hints.append({'index': index, 'kind': 'SHAPE_OR_MODE_UNKNOWN'})
            continue
        missing = sorted(set(variant['required']) - set(element))
        extra = sorted(set(element) - set(variant['properties']))
        if missing or extra:
            hints.append({'index': index, 'kind': 'FIELD_CONTRACT_MISMATCH',
                          'missingKeys': missing, 'unexpectedKeyCount': len(extra)})
        if len(hints) >= 12:
            break
    return {'elementCount': len(items), 'hints': hints,
            'checkedShapeFieldPresenceOnly': True,
            'geometryValidatedByThisReport': False, 'modelContentSaved': False}


def save_verified_design_in_studio(*, root: Path, design: dict, scene: dict,
                                   engine_commit: str, verified_against: dict | None = None) -> dict:
    """Preserve an already-compiled actual SOURCE result in a new local project.

    This only saves a source design; it NEVER approves construction, performs
    Minecraft I/O or changes earlier immutable revision snapshots.  The caller
    must have explicitly opted in and must already have verified the design
    against the pinned MAB compiler.  If another Studio instance has changed
    the active head during inference, refuse to replace that user's selection.
    """
    from app.revisions import RevisionStore
    from app.core import StudioError

    if not isinstance(scene, dict) or scene.get('designOnly') is not True or scene.get('readiness') != 'NOT_PREPARED':
        raise StudioError('施工不可のSOURCE検証結果だけを履歴へ保存できます')
    if (scene.get('worldContacts') != 0 or scene.get('worldCoordinates') is not None or
            scene.get('BuildPlan', {}).get('operations') != [] or
            scene.get('stats', {}).get('constructionApproved') is not False):
        raise StudioError('施工ゲートが閉じていない設計は保存しません')
    if not isinstance(design, dict) or design.get('designOnly') is not True:
        raise StudioError('AI設計がSOURCE設計専用ではありません')
    if verified_against is not None and (
            scene.get('sourceSha256') != verified_against.get('sourceSha256') or
            scene.get('geometrySha256') != verified_against.get('geometrySha256')):
        raise StudioError('再検証時のSOURCEと生成済みモデルが一致しません')
    store = RevisionStore(root / 'projects')
    # The head observed before inference is passed explicitly. A manual Studio
    # edit while the model runs must not be silently superseded.
    initial_head = verified_against.get('expectedStudioHead') if verified_against else None
    current = store.head()
    current_id = current['revisionId'] if current else None
    if current_id != initial_head:
        raise StudioError('生成中にStudioの履歴が変更されました。元の設計を保持して保存を中止しました')
    item = store.commit(design, scene, engine_commit=engine_commit, operation='new')
    return {'saved': True, 'projectId': item['projectId'],
            'revisionId': item['revisionId'], 'sourceSha256': item['sourceSha256'],
            'geometrySha256': item['geometrySha256'], 'sourceOnly': True,
            'constructionApproved': False, 'worldWrites': 0,
            'humanQualityReview': 'PENDING'}


def run_acceptance(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description='MAB Studio actual local-model acceptance (no Minecraft calls)')
    parser.add_argument('--acceptance', action='store_true', required=True)
    parser.add_argument('--provider', required=True, choices=('Ollama', 'LM Studio'))
    parser.add_argument('--model', required=True)
    parser.add_argument('--prompt', default=DEFAULT_PROMPT)
    parser.add_argument('--report', required=True)
    parser.add_argument('--console-progress', action='store_true',
                        help='Source acceptance: print counts, never model content')
    parser.add_argument('--save-to-studio', action='store_true',
                        help='On genuine SOURCE + GUI PASS, store the design as a new Studio project')
    parser.add_argument('--test-double', action='store_true',
                        help='Explicit localhost CI fixture, never a real LLM acceptance result')
    parser.add_argument('--fixture-port', type=int, default=None,
                        help='Only valid together with --test-double and model CI_TEST_DOUBLE')
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
        'studioProject': {'saved': False},
        'actualProviderRequestAttempted': False,
        'testDoubleUsed': bool(ns.test_double),
        'testDoubleRequestAttempted': False,
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
        if (ns.test_double and (ns.provider != 'Ollama' or ns.model != 'CI_TEST_DOUBLE'
                                  or ns.fixture_port is None or not (1 <= ns.fixture_port <= 65535))):
            raise StudioError('テスト用応答は明示的なOllamaテスト設定とポートのみ許可します')
        if not ns.test_double and (ns.fixture_port is not None or ns.model == 'CI_TEST_DOUBLE'):
            raise StudioError('CIの模擬AIを真正ローカルLLMとして実行することは禁止です')
        if ns.save_to_studio and (ns.test_double or ns.engine is not None):
            raise StudioError('Studioへの保存は固定エンジンを使用した真正モデルの受入試験だけに許可します')
        if not ns.model.strip() or not ns.prompt.strip() or len(ns.prompt) > 2000:
            raise StudioError('受入テストのモデル名または日本語指示が無効です')
        report['stage'] = 'PACKAGED_RUNTIME'
        report['bundle'] = verify_packaged_runtime()
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
        # Capture the active head BEFORE the expensive inference. Saving at the
        # end is refused if another app/CLI changed it in the meantime.
        expected_studio_head = None
        if ns.save_to_studio:
            from app.revisions import RevisionStore
            active = RevisionStore(manager.root / 'projects').head()
            expected_studio_head = active['revisionId'] if active else None
        report['stage'] = 'LOCAL_MODEL_REQUEST'
        # Exactly ONE explicit inference for diagnostic predictability. Retry is
        # separately available in ordinary GUI design, not hidden in this test.
        original_base = None
        if ns.test_double:
            # An isolated ephemeral loopback fixture must never be counted as a real model.
            from app import core
            original_base = core.OLLAMA_BASE
            core.OLLAMA_BASE = f'http://127.0.0.1:{ns.fixture_port}'
            report['testDoubleRequestAttempted'] = True
        else:
            report['actualProviderRequestAttempted'] = True
        llm_started = time.monotonic()
        try:
            def progress(note: str) -> None:
                # Counts only; never print original instructions or AI response.
                if ns.console_progress and sys.stdout is not None:
                    now = time.monotonic()
                    if now - progress.last_time >= 8:
                        print('[LOCAL MODEL] ' + note, flush=True)
                        progress.last_time = now
            progress.last_time = llm_started
            if ns.console_progress and sys.stdout is not None:
                print('[LOCAL MODEL] One inference started; no automatic retry.', flush=True)
            def metadata_received(receipt: dict) -> None:
                # Never persist original model text or user prompt.
                report['metadataAdjustments'] = receipt
            design = ask_llm(ns.provider, ns.model, ns.prompt,
                             on_progress=progress if ns.console_progress else None,
                             on_metadata=metadata_received)
        finally:
            # Keep elapsed time even when the model errors or hits a token limit;
            # otherwise the real-model diagnostic cannot distinguish those cases.
            report['providerRequestSeconds'] = round(time.monotonic() - llm_started, 2)
            if original_base is not None:
                core.OLLAMA_BASE = original_base
        report['stage'] = 'REAL_MAB_SOURCE_VERIFICATION'
        # This records only field names and element indices for a failed local
        # model candidate. No prompts, model JSON, coordinates or materials.
        report['candidateFieldDiagnostics'] = inspect_candidate_shape_fields(design)
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
        if ns.save_to_studio:
            report['stage'] = 'STUDIO_PROJECT_SAVE'
            # This persistence is source-only. The next normal Studio launch
            # re-opens and recompiles the latest project automatically.
            saved = save_verified_design_in_studio(
                root=manager.root, design=design, scene=scene,
                engine_commit=BASELINE_SHA,
                verified_against={**report['source'],
                                  'expectedStudioHead': expected_studio_head})
            report['studioProject'] = saved
            report['candidateFieldDiagnostics']['modelContentSaved'] = True
        report['outcome'] = ('PASS_TEST_DOUBLE_SOURCE_AND_GUI_ONLY' if ns.test_double
                             else 'PASS_SOURCE_AND_GUI_ONLY')
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
    return 0 if report['outcome'] in ('PASS_SOURCE_AND_GUI_ONLY',
                                       'PASS_TEST_DOUBLE_SOURCE_AND_GUI_ONLY') else 1
