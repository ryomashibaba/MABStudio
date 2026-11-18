"""Read-only saved SOURCE native proof, not real model or Windows EXE QA."""
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from app.core import BASELINE_SHA
from app.examples import example_library
from app.revisions import RevisionStore
from app import saved_native_selfcheck
from tests.test_gui_review_flow import _scene


def _create_fixture(tmp_path):
    design = example_library()
    scene = _scene(design)
    scene['primitives'] += [[8, 2, 9, 1, 0], [10, 5, 9, 0, 0], [8, 3, 9, 0, 0]]
    scene['stats']['voxels'] = len(scene['primitives'])
    scene['previewPalette'] = ['#b7a393', '#3c86ad']
    store = RevisionStore(tmp_path / 'projects')
    snap = store.commit(design, scene, engine_commit=BASELINE_SHA)
    return store, snap


@pytest.mark.skipif(os.name != 'nt' and not os.getenv('DISPLAY'), reason='Tk display unavailable')
def test_saved_revision_real_native_pixels_are_read_only(tmp_path):
    from app.native_viewer import native_dependencies_available
    if not native_dependencies_available():
        pytest.skip('VTK/Pillow optional dependencies absent')
    store, saved = _create_fixture(tmp_path)
    folder = tmp_path / 'projects'
    immutable = (folder/'revisions'/(saved['revisionId']+'.json'))
    before_sha = hashlib.sha256(immutable.read_bytes()).hexdigest()
    index_before = (folder/'project.json').read_bytes()
    result = saved_native_selfcheck.inspect_saved_revision(folder, saved['revisionId'])
    assert result['result'] == 'PASS_SAVED_REVISION_NATIVE_RENDER_ONLY'
    assert result['cells'] == 9
    assert result['sectionChecked'] and result['sectionCells'] < result['cells']
    assert result['cameraPixelChangeVerified']
    assert result['panPixelChangeVerified']
    assert result['renderTimings']['samples'] >= 4
    assert result['rendererDiagnostics']['classification'] in {
        'SOFTWARE_RENDERER', 'PHYSICAL_GPU_RENDERER', 'UNKNOWN_RENDERER'}
    assert result['hardwareGpuIndependentlyVerified'] == (
        result['rendererDiagnostics']['classification'] == 'PHYSICAL_GPU_RENDERER')
    assert result['interactionTimingsMs']['cameraRotation']['samples'] == 12
    assert result['interactionTimingsMs']['zoom']['samples'] == 12
    assert result['interactionTimingsMs']['pan']['samples'] == 12
    assert result['idle']['renderFrames'] == 0
    assert result['workingSetMb']['afterInteractions'] is None or result['workingSetMb']['afterInteractions'] > 0
    if os.name == 'nt':
        assert result['workingSetMb']['beforeScene'] > 0
        assert result['workingSetMb']['afterInteractions'] > 0
    assert result['revisionAndIndexUnchanged']
    assert not result['sourceRecompiledWithB143']
    assert result['modelRequests'] == result['worldWrites'] == result['minecraftWorldContacts'] == 0
    assert hashlib.sha256(immutable.read_bytes()).hexdigest() == before_sha
    assert (folder/'project.json').read_bytes() == index_before
    assert store.head()['revisionId'] == saved['revisionId']


def test_saved_revision_rejects_modified_immutable_snapshot(tmp_path):
    store, saved = _create_fixture(tmp_path)
    path = tmp_path/'projects'/'revisions'/(saved['revisionId']+'.json')
    payload = json.loads(path.read_text(encoding='utf-8'))
    payload['scene']['primitives'][0][0] += 1
    path.write_text(json.dumps(payload), encoding='utf-8')
    with pytest.raises(Exception, match='改変'):
        saved_native_selfcheck.inspect_saved_revision(tmp_path/'projects', saved['revisionId'])


def test_saved_cli_fail_closed_when_report_inside_original_project(tmp_path):
    store, saved = _create_fixture(tmp_path)
    protected = tmp_path/'projects'/'diagnostics.json'
    result = saved_native_selfcheck.main(['--project-root', str(store.folder),
            '--revision-id', saved['revisionId'], '--report', str(protected)])
    assert result == 1 and not protected.exists()


def test_windowed_cli_output_is_optional(tmp_path, monkeypatch):
    report = tmp_path/'failed.json'
    monkeypatch.setattr(sys, 'stdout', None)
    assert saved_native_selfcheck.main(['--revision-id', 'bad', '--report', str(report)]) == 1
    assert json.loads(report.read_text(encoding='utf-8'))['worldWrites'] == 0
    from app import native_selfcheck
    monkeypatch.setattr(native_selfcheck, 'inspect_native_viewer', lambda **kw:{'result': 'PASS_NATIVE_READONLY_SELF_CHECK'})
    assert native_selfcheck.main(['--report', str(tmp_path/'native.json')]) == 0


def test_windows_qa_offers_opt_in_saved_revision_without_auto_model(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = (root/'RUN_WINDOWS_NATIVE_QA.ps1').read_text(encoding='utf-8-sig')
    assert 'param(' in source
    assert '--saved-native-selfcheck' in source
    assert "if ($SavedRevisionId)" in source
    assert 'PASS_EXTRACTED_EXE_READONLY_SAVED_REVISION' in source
    batch = (root/'RUN_WINDOWS_NATIVE_QA.bat').read_bytes()
    assert b'\r\n' in batch and b' %*\r\n' in batch
    assert b'MAB_STUDIO_NO_PAUSE' in batch
