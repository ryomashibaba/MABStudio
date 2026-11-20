"""Preserve a SOURCE-only real-model result so it can be reopened in Studio.

A deterministic model stub is used in unit tests; these do NOT establish
Gemma output quality or independently identify the actual model on a PC.
"""
import copy
import json
import os
from pathlib import Path
import pytest

from app import acceptance
from app import core
from app.examples import example_library
from app.revisions import RevisionStore
from tests.test_gui_review_flow import _scene


def test_saving_success_keeps_previous_immutable_design(tmp_path):
    old = example_library()
    older_scene = _scene(old)
    fresh = copy.deepcopy(old)
    fresh['title'] = 'new source-only observation tower'
    new_scene = _scene(fresh)
    store = RevisionStore(tmp_path / 'projects')
    original = store.commit(old, older_scene, engine_commit=core.BASELINE_SHA)
    saved = acceptance.save_verified_design_in_studio(
        root=tmp_path, design=fresh, scene=new_scene,
        engine_commit=core.BASELINE_SHA,
        verified_against={'sourceSha256': new_scene['sourceSha256'],
                          'geometrySha256': new_scene['geometrySha256'],
                          'expectedStudioHead': original['revisionId']})
    assert saved['saved'] is True
    assert saved['constructionApproved'] is False
    assert saved['worldWrites'] == 0
    assert saved['humanQualityReview'] == 'PENDING'
    assert store.head()['revisionId'] == saved['revisionId']
    assert store.head()['design'] == fresh
    assert store.read_revision(original['revisionId'])['design'] == old
    assert len(store.list_saved()) == 2


def test_handoff_refuses_stale_session_or_modified_geometry(tmp_path):
    store = RevisionStore(tmp_path / 'projects')
    design = example_library()
    scene = _scene(design)
    old = store.commit(design, scene, engine_commit=core.BASELINE_SHA)
    other = store.commit(design, scene, engine_commit=core.BASELINE_SHA)
    with pytest.raises(core.StudioError, match='履歴が変更'):
        acceptance.save_verified_design_in_studio(
            root=tmp_path, design=design, scene=scene,
            engine_commit=core.BASELINE_SHA,
            verified_against={'sourceSha256': scene['sourceSha256'],
                              'geometrySha256': scene['geometrySha256'],
                              'expectedStudioHead': old['revisionId']})
    assert store.head()['revisionId'] == other['revisionId']
    assert len(store.list_saved()) == 2
    with pytest.raises(core.StudioError, match='SOURCE'):
        acceptance.save_verified_design_in_studio(
            root=tmp_path, design=design, scene=scene,
            engine_commit=core.BASELINE_SHA,
            verified_against={'sourceSha256': '0' * 64,
                              'geometrySha256': scene['geometrySha256'],
                              'expectedStudioHead': other['revisionId']})
    assert len(store.list_saved()) == 2


def test_handoff_rejects_unsafe_source(tmp_path):
    design = example_library()
    scene = _scene(design)
    scene['BuildPlan'] = {'operations': [{'cmd':'setblock'}]}
    with pytest.raises(core.StudioError, match='施工'):
        acceptance.save_verified_design_in_studio(
            root=tmp_path, design=design, scene=scene,
            engine_commit=core.BASELINE_SHA,
            verified_against={'expectedStudioHead': None})
    assert not (tmp_path / 'projects').exists()


def test_source_cli_refuses_mock_save_before_model_request(tmp_path):
    report = tmp_path / 'report.json'
    outcome = acceptance.run_acceptance([
        '--acceptance', '--test-double', '--fixture-port','32512',
        '--provider','Ollama','--model','CI_TEST_DOUBLE',
        '--save-to-studio','--report',str(report)])
    data = json.loads(report.read_text(encoding='utf-8'))
    assert outcome == 1
    assert data['stage'] == 'PREFLIGHT'
    assert data['actualProviderRequestAttempted'] is False
    assert data['studioProject']['saved'] is False
    assert data['worldWrites'] == 0


@pytest.mark.real_engine
def test_integrated_source_gui_and_studio_handoff(tmp_path, monkeypatch):
    # Uses the exact pinned, SHA-verified B143 module (if available) and Tk;
    # only the model HTTP response is stubbed. Must be run under Xvfb on Linux.
    if os.name != 'nt' and not os.environ.get('DISPLAY'):
        pytest.skip('GUI requires a display')
    folder = os.getenv('MAB_B143_VERIFIED_ENGINE')
    if not folder or not (Path(folder)/core.ENGINE_FILE).is_file():
        pytest.skip('Pinned B143 fixture not mounted')
    from tests.test_local_llm_flow import DESIGN
    expected = acceptance.run_bridge(Path(folder), DESIGN)

    class StubManager:
        def __init__(self):
            self.root = tmp_path
            self.active_path = Path(folder)

    monkeypatch.setattr(acceptance, 'EngineManager', StubManager)
    monkeypatch.setattr(acceptance, 'verify_packaged_runtime', lambda: {'mode': 'SOURCE'})
    monkeypatch.setattr(acceptance, 'compatibility_check', lambda engine: {'geometrySha256': 'smoke'})
    calls = []
    def fake_model(*args, **kwargs):
        calls.append((args, kwargs))
        return copy.deepcopy(DESIGN)
    monkeypatch.setattr(acceptance, 'ask_llm', fake_model)
    # The real CLI now enforces a read-only loaded-model preflight.  This
    # integration fixture uses a fake model, so fake the preflight as well;
    # never connect to LM Studio or claim a genuine provider inference.
    monkeypatch.setattr(acceptance, 'verify_loaded_model',
                        lambda provider, model: {'loaded': True,
                                                 'verifiedBy': 'TEST_DOUBLE_PREFLIGHT',
                                                 'inferenceModel': model})
    report_path = tmp_path / 'real-path-simulated-model.json'
    result = acceptance.run_acceptance([
        '--acceptance','--save-to-studio', '--provider','LM Studio',
        '--model','gemma-4-26b-a4b-it-qat', '--report',str(report_path)])
    receipt = json.loads(report_path.read_text(encoding='utf-8'))
    assert result == 0 and receipt['stage'] == 'COMPLETE'
    assert receipt['outcome'] == 'PASS_SOURCE_AND_GUI_ONLY'
    assert receipt['sourceValid'] and receipt['guiRendered']
    assert len(calls) == 1
    assert receipt['studioProject']['saved']
    assert receipt['candidateFieldDiagnostics']['modelContentSaved'] is True
    saved = RevisionStore(tmp_path / 'projects').head()
    assert saved['design'] == DESIGN
    assert saved['sourceSha256'] == expected['sourceSha256']
    assert saved['geometrySha256'] == expected['geometrySha256']
    assert saved['worldWrites'] == 0
