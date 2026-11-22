"""Unapproved proposal packages stay outside approved RevisionStore history."""
from __future__ import annotations

import copy
import json
import os

import pytest

from app import core, main as studio_main
from app.examples import example_library
from app.project_io import read_unapproved_draft, save_unapproved_draft
from tests.test_gui_review_flow import _complete, _scene, studio


def _metadata(**overrides):
    value = {
        'draftKind': 'new', 'origin': 'v11-external',
        'engineSha': core.BASELINE_SHA, 'expectedHead': None,
        'expectedHeadSnapshotSha256': None, 'provider': 'V11_EXTERNAL_SOURCE',
        'model': '',
    }
    value.update(overrides)
    return value


def test_unapproved_package_round_trip_and_tamper_hold(tmp_path):
    design = example_library()
    scene = _scene(design)
    destination = tmp_path / 'lakefront-unapproved.json'
    saved = save_unapproved_draft(destination, design, _metadata(),
                                  scene['sourceSha256'], scene['geometrySha256'],
                                  lambda _: scene)
    loaded, metadata = read_unapproved_draft(saved)
    assert loaded == design
    assert metadata['origin'] == 'v11-external'
    assert metadata['sourceSha256'] == scene['sourceSha256']
    assert metadata['geometrySha256'] == scene['geometrySha256']

    package = json.loads(saved.read_text(encoding='utf-8'))
    package['design']['title'] = 'edited'
    saved.write_text(json.dumps(package), encoding='utf-8')
    with pytest.raises(core.StudioError, match='改変|破損|内容改変'):
        read_unapproved_draft(saved)


def test_unapproved_package_holds_wrong_engine_and_compile_hash(tmp_path):
    design = example_library()
    scene = _scene(design)
    with pytest.raises(core.StudioError, match='固定B143'):
        save_unapproved_draft(tmp_path / 'wrong-engine.json', design,
            _metadata(engineSha='0' * 40), scene['sourceSha256'],
            scene['geometrySha256'], lambda _: scene)
    with pytest.raises(core.StudioError, match='SOURCE/geometry'):
        save_unapproved_draft(tmp_path / 'wrong-sha.json', design,
            _metadata(), scene['sourceSha256'], '0' * 64, lambda _: scene)


def test_gui_saves_and_reopens_as_unapproved_without_history_write(studio, monkeypatch):
    if os.name != 'nt' and not os.getenv('DISPLAY'):
        pytest.skip('GUI test needs DISPLAY (use xvfb-run -a on Linux)')
    app, _ = studio
    monkeypatch.setattr(studio_main.messagebox, 'showinfo', lambda *a, **k: None)
    design = copy.deepcopy(example_library())
    design['title'] = '湖畔の船史資料館'
    scene = _scene(design)
    monkeypatch.setattr(studio_main, 'run_bridge', lambda _, d: _scene(d))
    head_before = app.history.head()
    revisions_before = sorted(p.name for p in (app.manager.root / 'projects' / 'revisions').glob('*.json'))
    app._hold_generated_candidate(design, scene, 'V11_EXTERNAL_SOURCE', '',
                                  head_before['revisionId'])
    app.export_draft_source()
    _complete(app)
    paths = list((app.manager.root / 'unapproved-drafts').glob('*.json'))
    assert len(paths) == 1
    assert app.history.head()['revisionId'] == head_before['revisionId']
    assert sorted(p.name for p in (app.manager.root / 'projects' / 'revisions').glob('*.json')) == revisions_before
    app.pending_revision = None
    assert app._open_saved_unapproved_filename(paths[0])
    _complete(app)
    assert app.pending_revision['kind'] == 'new'
    assert app.pending_revision['provider'] == 'V11_EXTERNAL_SOURCE'
    assert app.pending_revision['design'] == design
    assert app.pending_revision['scene']['sourceSha256'] == scene['sourceSha256']
    assert app.pending_revision['scene']['geometrySha256'] == scene['geometrySha256']
    assert app.history.head()['revisionId'] == head_before['revisionId']
    assert sorted(p.name for p in (app.manager.root / 'projects' / 'revisions').glob('*.json')) == revisions_before


def test_unapproved_revision_round_trip_keeps_diff_and_base_head(studio, monkeypatch):
    app, _ = studio
    monkeypatch.setattr(studio_main.messagebox, 'showinfo', lambda *a, **k: None)
    monkeypatch.setattr(studio_main, 'run_bridge', lambda _, d: _scene(d))
    head = app.history.head()
    app.revise_design()
    _complete(app)
    candidate = app.pending_revision
    assert candidate['kind'] == 'revision'
    assert candidate['expectedHead'] == head['revisionId']
    app.export_draft_source()
    _complete(app)
    path = next((app.manager.root / 'unapproved-drafts').glob('*.json'))
    design, metadata = read_unapproved_draft(path)
    assert metadata['draftKind'] == 'revision'
    assert metadata['origin'] == 'revision'
    assert metadata['parentId'] == metadata['expectedHead'] == head['revisionId']
    assert design == candidate['design']

    app.pending_revision = None
    assert app._open_saved_unapproved_filename(path)
    _complete(app)
    restored = app.pending_revision
    assert restored['kind'] == 'revision'
    assert restored['parentId'] == head['revisionId']
    assert restored['delta']['newGeometrySha256'] == restored['scene']['geometrySha256']
    assert app.history.head()['revisionId'] == head['revisionId']


def test_saved_draft_stops_when_approved_head_has_changed(studio, monkeypatch):
    app, _ = studio
    monkeypatch.setattr(studio_main.messagebox, 'showerror', lambda *a, **k: None)
    design = copy.deepcopy(example_library())
    design['title'] = 'stale lakefront draft'
    scene = _scene(design)
    monkeypatch.setattr(studio_main, 'run_bridge', lambda _, d: _scene(d))
    head = app.history.head()
    path = save_unapproved_draft(
        app.manager.root / 'unapproved-drafts' / 'stale.json', design,
        _metadata(expectedHead=head['revisionId'],
                  expectedHeadSnapshotSha256=head['snapshotSha256']),
        scene['sourceSha256'], scene['geometrySha256'], lambda _: scene)
    # Advance the approved history through the existing explicit approval path.
    revised = copy.deepcopy(example_library())
    revised['title'] = 'approved next head'
    app.history.commit(revised, _scene(revised), engine_commit=core.BASELINE_SHA,
                       expected_head_id=head['revisionId'])
    before = app.history.head()['revisionId']
    app._open_saved_unapproved_filename(path)
    _complete(app)
    assert app.pending_revision is None
    assert app.history.head()['revisionId'] == before
    assert path.exists()
