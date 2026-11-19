"""A B143 PASS is not an artistic approval or permission to write history.

Fixture model and SOURCE bridge, NOT a real Windows/Ollama inference.
"""
from __future__ import annotations
import copy
import threading

from app import main as studio_main, core
from app.examples import example_library
from tests.test_gui_review_flow import studio, _complete, _scene


def _design(title):
    d=copy.deepcopy(example_library())
    d['title']=title
    return d


def test_initial_new_generation_requires_approval_and_keeps_existing_output(studio, monkeypatch):
    app,_=studio
    first=app.history.head()
    output=(app.manager.root/'outputs'/'latest-design.json').read_bytes()
    generated=_design('3Dで初めて確認する新築')
    monkeypatch.setattr(studio_main, 'ask_llm', lambda *a, **kw: copy.deepcopy(generated))
    monkeypatch.setattr(studio_main, 'run_bridge', lambda _, d: _scene(d))
    app.generate_btn.invoke(); _complete(app)
    assert app.pending_revision['kind']=='new'
    assert app.pending_revision['design']['title']==generated['title']
    assert app.view_mode.get()=='修正案（未承認）'
    assert app.preview.scene['title']==generated['title']
    assert app.current_snapshot['revisionId']==first['revisionId']
    assert app.history.head()['revisionId']==first['revisionId']
    assert (app.manager.root/'outputs'/'latest-design.json').read_bytes()==output
    assert 'disabled' not in app.approve_draft_btn.state()
    assert 'disabled' not in app.reject_draft_btn.state()
    assert 'disabled' not in app.revise_btn.state()  # Can improve before first save
    assert 'disabled' in app.generate_btn.state()  # Can't silently replace the draft
    assert 'disabled' in app.export_btn.state()  # Unapproved draft is not exportable
    app.compare_draft_btn.invoke()
    assert app.preview.scene['title']==first['design']['title']
    app.compare_draft_btn.invoke()
    assert app.preview.scene['title']==generated['title']
    app.approve_draft_btn.invoke(); _complete(app)
    assert app.pending_revision is None
    assert app.history.head()['design']['title']==generated['title']
    assert app.history.head()['worldWrites']==0
    assert app.history.head()['parentId'] is None
    assert app.view_mode.get()=='建築のみ'
    assert (app.manager.root/'outputs'/'latest-design.json').read_bytes()!=output


def test_reject_new_draft_preserves_approved_building_and_output(studio, monkeypatch):
    app,_=studio
    before=app.history.head()
    output=(app.manager.root/'outputs'/'latest-design.json').read_bytes()
    monkeypatch.setattr(studio_main,'ask_llm',lambda *a,**kw:_design('不要な新案'))
    monkeypatch.setattr(studio_main,'run_bridge',lambda _,d:_scene(d))
    app.generate(); _complete(app)
    assert app.history.head()['revisionId']==before['revisionId']
    app.reject_draft_btn.invoke()
    assert app.pending_revision is None
    assert app.preview.scene['title']==before['design']['title']
    assert app.history.head()['revisionId']==before['revisionId']
    assert (app.manager.root/'outputs'/'latest-design.json').read_bytes()==output


def test_modify_new_draft_without_approving_then_approve(studio, monkeypatch):
    app,_=studio
    original=app.history.head()
    initial=_design('初回の新案')
    corrected=_design('階段と窓を改良した新案')
    model_calls=[]
    def model(*a,**kw):
        model_calls.append(kw)
        return copy.deepcopy(initial if len(model_calls)==1 else corrected)
    monkeypatch.setattr(studio_main, 'ask_llm', model)
    monkeypatch.setattr(studio_main, 'run_bridge', lambda _, d: _scene(d))
    app.generate(); _complete(app)
    app.prompt.delete('1.0','end');app.prompt.insert('1.0','階段と窓を改善して')
    app.revise_btn.invoke(); _complete(app)
    assert len(model_calls)==2
    assert model_calls[1]['revision_base']==initial
    assert model_calls[1]['revision_mode']=='delta'
    assert app.pending_revision['design']['title']==corrected['title']
    assert app.preview.scene['title']==corrected['title']
    assert app.history.head()['revisionId']==original['revisionId']
    app.approve_draft_btn.invoke(); _complete(app)
    assert app.history.head()['design']['title']==corrected['title']
    assert app.history.head()['parentId'] is None


def test_failed_unapproved_edit_does_not_destroy_candidate(studio, monkeypatch):
    app,_=studio
    draft=_design('保存前の元案')
    monkeypatch.setattr(studio_main, 'ask_llm', lambda *a, **kw: copy.deepcopy(draft))
    monkeypatch.setattr(studio_main, 'run_bridge', lambda _, d: _scene(d))
    app.generate(); _complete(app)
    old_candidate=app.pending_revision
    old_head=app.history.head()['revisionId']
    monkeypatch.setattr(studio_main, 'ask_llm', lambda *a, **kw: (_ for _ in ()).throw(
        core.StudioError('モデルが応答しませんでした')))
    monkeypatch.setattr(studio_main.messagebox, 'showerror', lambda *a:None)
    app.revise_btn.invoke(); _complete(app)
    assert app.pending_revision is old_candidate
    assert app.preview.scene['title']==draft['title']
    assert app.history.head()['revisionId']==old_head


def test_external_head_change_blocks_first_approval(studio,monkeypatch):
    app,_=studio
    generated=_design('既存履歴に競合する案')
    monkeypatch.setattr(studio_main,'ask_llm',lambda *a,**kw:copy.deepcopy(generated))
    monkeypatch.setattr(studio_main,'run_bridge',lambda _,d:_scene(d))
    errors=[]
    monkeypatch.setattr(studio_main.messagebox,'showerror',lambda *a:errors.append(a))
    app.generate();_complete(app)
    assert app.pending_revision is not None
    external=_design('外部で保存された建築')
    app.history.commit(external,_scene(external),engine_commit=core.BASELINE_SHA,operation='new')
    latest=app.history.head()['revisionId']
    app.approve_draft_btn.invoke();_complete(app)
    assert app.history.head()['revisionId']==latest
    assert app.pending_revision is not None
    assert '失敗' in app.status.get() or errors
    app.reject_draft_btn.invoke()
    assert app.pending_revision is None


def test_modify_cancel_preserves_unapproved_draft(studio,monkeypatch):
    app,_=studio
    initial=_design('キャンセル前の生成案')
    entered=threading.Event();release=threading.Event()
    calls=[]
    def model(*a,**kw):
        calls.append(kw)
        if len(calls)==2:
            entered.set()
            release.wait(2)
        return copy.deepcopy(initial)
    monkeypatch.setattr(studio_main,'ask_llm',model)
    monkeypatch.setattr(studio_main,'run_bridge',lambda _,d:_scene(d))
    app.generate();_complete(app)
    original_head=app.history.head()['revisionId']
    app.revise_btn.invoke()
    assert entered.wait(1)
    app.cancel_btn.invoke()
    release.set(); _complete(app)
    assert len(calls)==2
    assert app.pending_revision['design']['title']==initial['title']
    assert app.history.head()['revisionId']==original_head


def test_new_approval_controls_fit_minimum_window(studio, monkeypatch):
    app,_=studio
    app.geometry('990x690')
    app.update()
    monkeypatch.setattr(studio_main,'ask_llm',lambda *a,**kw:_design('画面幅確認'))
    monkeypatch.setattr(studio_main,'run_bridge',lambda _,d:_scene(d))
    app.generate();_complete(app)
    app.update_idletasks()
    right_edge=app.winfo_rootx()+app.winfo_width()
    for widget in (app.reviewbar_label,app.approve_draft_btn,
                   app.reject_draft_btn,app.compare_draft_btn):
        assert widget.winfo_ismapped()
        assert widget.winfo_width() >= 80
        assert widget.winfo_rootx()+widget.winfo_width() <= right_edge


def test_first_ever_building_is_viewable_and_not_saved_until_approval(tmp_path,monkeypatch):
    """Fresh install with no saved project: there is no previous scene to draw."""
    class Manager:
        root=tmp_path
        active_sha=core.BASELINE_SHA
        active_path=tmp_path/'fixed-engine'
        state={'tracked_branch':core.DEFAULT_BRANCH}
    monkeypatch.setattr(studio_main, 'EngineManager', Manager)
    monkeypatch.setattr(studio_main, 'verify_loaded_model',
                        lambda provider, model: {'inferenceModel':model, 'loaded':True})
    draft=_design('初回の建築')
    monkeypatch.setattr(studio_main, 'ask_llm',lambda *args,**kw:copy.deepcopy(draft))
    monkeypatch.setattr(studio_main, 'run_bridge',lambda _,d:_scene(d))
    app=studio_main.Studio()
    app.model.set('mock-loaded-model')
    try:
        assert app.scene is None
        assert app.history.head() is None
        app.generate();_complete(app)
        assert app.pending_revision['kind']=='new'
        assert app.preview.scene['title']==draft['title']
        assert app.history.head() is None
        assert 'disabled' in app.compare_draft_btn.state()
        assert 'disabled' in app.export_btn.state()
        assert not (tmp_path/'outputs'/'latest-design.json').exists()
        app.approve_draft_btn.invoke();_complete(app)
        assert app.history.head()['design']['title']==draft['title']
        assert app.pending_revision is None
        assert (tmp_path/'outputs'/'latest-design.json').exists()
    finally:
        app.destroy()


def test_new_draft_comparison_never_displays_unrelated_approved_building(studio, monkeypatch):
    app, _ = studio
    old = app.history.head()['design']['title']
    new = _design('いま承認対象の新しい建築')
    monkeypatch.setattr(studio_main, 'ask_llm', lambda *a, **k: copy.deepcopy(new))
    monkeypatch.setattr(studio_main, 'run_bridge', lambda _, d: _scene(d))
    app.generate(); _complete(app)
    assert old != new['title']
    for mode in ('変更差分', '地形＋建築'):
        app.view_mode.set(mode)
        app._refresh_view()
        assert app.preview.scene['title'] == new['title']
        assert app.history.head()['design']['title'] == old


def test_approval_switches_back_to_actual_candidate_before_history_write(studio, monkeypatch):
    app, _ = studio
    old_head = app.history.head()['revisionId']
    new = _design('誤承認を防止する新案')
    monkeypatch.setattr(studio_main, 'ask_llm', lambda *a, **k: copy.deepcopy(new))
    monkeypatch.setattr(studio_main, 'run_bridge', lambda _, d: _scene(d))
    app.generate(); _complete(app)
    app.compare_draft_btn.invoke()
    assert app.view_mode.get() == '建築のみ'
    app.approve_draft_btn.invoke()
    assert app.history.head()['revisionId'] == old_head
    assert app.pending_revision is not None
    assert app.view_mode.get() == '修正案（未承認）'
    assert app.preview.scene['title'] == new['title']
    assert 'もう一度' in app.status.get()
    app.approve_draft_btn.invoke(); _complete(app)
    assert app.history.head()['design']['title'] == new['title']


def test_revision_delta_cannot_be_approved_without_showing_actual_revised_scene(studio):
    app, _ = studio
    old_head = app.history.head()['revisionId']
    app.revise_design(); _complete(app)
    assert app.pending_revision is not None
    app.view_mode.set('変更差分')
    app._refresh_view()
    app.approve_draft_btn.invoke()
    assert app.history.head()['revisionId'] == old_head
    assert app.view_mode.get() == '修正案（未承認）'
    app.approve_draft_btn.invoke(); _complete(app)
    assert app.history.head()['revisionId'] != old_head
