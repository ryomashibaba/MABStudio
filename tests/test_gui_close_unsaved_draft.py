"""Window-manager close protects unsaved Studio work, not world/build operations."""
from __future__ import annotations

import copy
import threading

from app import main as studio_main
from app.examples import example_library
from tests.test_gui_review_flow import studio, _complete, _scene


def test_clean_approved_project_closes_without_extra_question(studio, monkeypatch):
    app, _ = studio
    called = []
    monkeypatch.setattr(studio_main.messagebox, 'askyesno',
                        lambda *a, **kw: called.append('question') or False)
    app.request_close()
    assert app._closed is True
    assert called == []


def test_new_unapproved_draft_requires_consent_and_preserves_saved_head(studio, monkeypatch):
    app, _ = studio
    saved = app.history.head()['revisionId']
    source = (app.manager.root/'outputs'/'latest-design.json').read_bytes()
    monkeypatch.setattr(studio_main, 'run_bridge', lambda _, d: _scene(d))
    new = copy.deepcopy(example_library())
    new['title'] = '保存していない新築'
    monkeypatch.setattr(studio_main, 'ask_llm', lambda *a, **k: new)
    app.generate(); _complete(app)
    assert app.pending_revision is not None
    prompts = []
    monkeypatch.setattr(studio_main.messagebox, 'askyesno',
                        lambda title, message, **kw: prompts.append(message) or False)
    app.request_close()
    assert app.winfo_exists() == 1
    assert '未承認の建築案' in prompts[0]
    assert app.history.head()['revisionId'] == saved
    assert (app.manager.root/'outputs'/'latest-design.json').read_bytes() == source
    monkeypatch.setattr(studio_main.messagebox, 'askyesno',
                        lambda *a, **kw: True)
    app.request_close()
    assert app._closed is True
    assert app.history.head()['revisionId'] == saved
    assert (app.manager.root/'outputs'/'latest-design.json').read_bytes() == source


def test_rejected_source_candidate_requires_confirmation(studio, monkeypatch):
    app, _ = studio
    saved = app.history.head()['revisionId']
    app.failed_generation = {'design': {'id': 'bad'}, 'error': 'B143_BOUNDS'}
    questions = []
    monkeypatch.setattr(studio_main.messagebox, 'askyesno',
                        lambda *a, **k: questions.append(a[1]) or False)
    app.request_close()
    assert '検証不合格' in questions[0]
    assert app.failed_generation is not None
    assert app.history.head()['revisionId'] == saved
    assert app._closed is False


def test_cancelable_ai_operation_does_not_cancel_without_confirmation(studio, monkeypatch):
    app, _ = studio
    token = threading.Event()
    app.busy = True
    app._active_cancel_event = token
    monkeypatch.setattr(studio_main.messagebox, 'askyesno',
                        lambda *a, **k: False)
    app.request_close()
    assert not token.is_set()
    assert app._closed is False
    monkeypatch.setattr(studio_main.messagebox, 'askyesno',
                        lambda *a, **k: True)
    app.request_close()
    assert token.is_set()
    assert app._closed is True


def test_non_cancelable_source_operation_cannot_be_interrupted_by_window_x(studio, monkeypatch):
    app, _ = studio
    app.busy = True
    app._active_cancel_event = None
    messages = []
    monkeypatch.setattr(studio_main.messagebox, 'showinfo',
                        lambda *a, **kw: messages.append(a))
    monkeypatch.setattr(studio_main.messagebox, 'askyesno',
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError('should not ask')))
    app.request_close()
    assert app._closed is False
    assert app.winfo_exists() == 1
    assert messages and '処理中' in messages[0][0]


def test_previewing_offline_sample_never_silently_changes_saved_project(studio, monkeypatch):
    app, _ = studio
    head_before = app.history.head()['revisionId']
    old_source = (app.manager.root/'outputs'/'latest-design.json').read_bytes()
    revisions = list((app.manager.root/'projects'/'revisions').glob('*.json'))
    monkeypatch.setattr(studio_main, 'run_bridge', lambda _, d: _scene(d))
    app.sample_btn.invoke(); _complete(app)
    assert app.pending_revision is not None
    assert app.pending_revision['kind'] == 'new'
    assert app.pending_revision['provider'] == 'OFFLINE_SAMPLE'
    assert app.preview.scene['title'] == example_library()['title']
    assert app.history.head()['revisionId'] == head_before
    assert list((app.manager.root/'projects'/'revisions').glob('*.json')) == revisions
    assert (app.manager.root/'outputs'/'latest-design.json').read_bytes() == old_source
    app.reject_draft_btn.invoke()
    assert app.history.head()['revisionId'] == head_before
    assert (app.manager.root/'outputs'/'latest-design.json').read_bytes() == old_source


def test_offline_sample_can_be_explicitly_approved_after_preview(studio, monkeypatch):
    app, _ = studio
    old_head = app.history.head()['revisionId']
    monkeypatch.setattr(studio_main, 'run_bridge', lambda _, d: _scene(d))
    app.sample_btn.invoke(); _complete(app)
    assert app.history.head()['revisionId'] == old_head
    app.approve_draft_btn.invoke(); _complete(app)
    saved = app.history.head()
    assert saved['revisionId'] != old_head
    assert saved['design']['title'] == example_library()['title']
    assert saved['worldWrites'] == 0
    assert app.pending_revision is None


def test_first_ever_sample_requires_approval_to_create_any_project(tmp_path, monkeypatch):
    """Even a fresh install must not save when user only clicks 'view sample'."""
    from app import core
    class Manager:
        root = tmp_path
        active_sha = core.BASELINE_SHA
        active_path = tmp_path / 'fixed-engine'
        state = {'tracked_branch': core.DEFAULT_BRANCH}
    monkeypatch.setattr(studio_main, 'EngineManager', Manager)
    monkeypatch.setattr(studio_main, 'run_bridge', lambda _, d: _scene(d))
    app = studio_main.Studio()
    try:
        assert app.history.head() is None
        app.sample_btn.invoke(); _complete(app)
        assert app.pending_revision is not None
        assert app.history.head() is None
        assert not (tmp_path/'outputs'/'latest-design.json').exists()
        app.approve_draft_btn.invoke(); _complete(app)
        assert app.history.head() is not None
        assert app.history.head()['worldWrites'] == 0
    finally:
        app.destroy()
