"""In-flight new-design response must not silently supersede another active project."""
from __future__ import annotations
import copy
from app import core, main as studio_main
from tests.test_gui_review_flow import studio, _complete, _scene


def test_new_design_model_result_cannot_replace_externally_switched_head(studio, monkeypatch):
    app, _ = studio
    initial = app.history.head()
    design = copy.deepcopy(initial['design'])
    design['title'] = 'External session approved building'
    new_design = copy.deepcopy(initial['design'])
    new_design['title'] = 'New AI answer'

    def model_answer(*args, **kwargs):
        app.history.commit(design, _scene(design), engine_commit=core.BASELINE_SHA, operation='new')
        return new_design

    monkeypatch.setattr(studio_main, 'ask_llm', model_answer)
    monkeypatch.setattr(studio_main, 'run_bridge', lambda eng, document: _scene(document))
    assert 'disabled' not in app.generate_btn.state()
    app.generate_btn.invoke()
    _complete(app)
    latest = app.history.head()
    assert latest['design']['title'] == 'External session approved building'
    assert app.current_snapshot['revisionId'] == initial['revisionId']
    assert app.design['title'] == initial['design']['title']
    assert '履歴を保護' in app.status.get()
    assert len(app.history.list_saved()) == 2
    assert not any(item['title'] == 'New AI answer' for item in app.history.list_saved())


def test_new_revision_store_head_lease_without_side_effect(tmp_path):
    from app.examples import example_library
    from app.revisions import RevisionStore
    import pytest
    store = RevisionStore(tmp_path / 'projects')
    design = example_library()
    old = store.commit(design, _scene(design), engine_commit=core.BASELINE_SHA)
    updated = store.commit(design, _scene(design), engine_commit=core.BASELINE_SHA)
    before = set((store.folder / 'revisions').glob('*.json'))
    with pytest.raises(core.StudioError, match='履歴を更新'):
        store.commit(design, _scene(design), engine_commit=core.BASELINE_SHA,
                     expected_head_id=old['revisionId'])
    assert store.head()['revisionId'] == updated['revisionId']
    assert set((store.folder / 'revisions').glob('*.json')) == before
