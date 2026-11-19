"""Bounded automatic recovery plus explicit fallback for genuine B143 refusals.

The local model and compiler are doubles; this is NOT Windows/native AI QA.
"""
from __future__ import annotations
import copy
import time

from tests.test_gui_review_flow import studio as studio, _scene
from app import core, main as studio_main


def _complete(app):
    deadline=time.monotonic()+4.0
    while app.busy and time.monotonic()<deadline:
        app.update()
        time.sleep(.015)
    app.update()
    assert not app.busy


def _draft(title):
    from app.examples import example_library
    d=copy.deepcopy(example_library())
    d['title']=title
    return d


def test_invalid_generation_auto_repairs_once_then_requires_approval(studio,monkeypatch):
    app,_=studio
    invalid=_draft('消防分署（座標不合格）')
    fixed=_draft('消防分署（座標修正済）')
    initial=app.history.head()
    outputs=(app.manager.root/'outputs'/'latest-design.json').read_bytes()
    model_calls=[]
    def model(provider, model, instruction, **kw):
        model_calls.append(kw)
        return copy.deepcopy(invalid if len(model_calls)==1 else fixed)
    def bridge(engine, design):
        if design['title']==invalid['title']:
            raise core.StudioError('B143_OUTSIDE_LOCAL_BOUNDS: antenna Y=24')
        return _scene(design)
    monkeypatch.setattr(studio_main,'ask_llm',model)
    monkeypatch.setattr(studio_main,'run_bridge',bridge)
    app.generate()
    _complete(app)
    assert len(model_calls)==2  # Second request is disclosed beside Design button.
    assert app.history.head()['revisionId']==initial['revisionId']
    assert app.current_snapshot['revisionId']==initial['revisionId']
    assert (app.manager.root/'outputs'/'latest-design.json').read_bytes()==outputs
    assert app.failed_generation is None
    assert app.repair_generation_btn.winfo_manager()==''
    assert model_calls[1]['previous_design']==invalid
    assert 'B143_OUTSIDE_LOCAL_BOUNDS' in model_calls[1]['error']
    assert 'revision_base' not in model_calls[1]  # Repair invalid new design; no approved revision base.
    assert app.failed_generation is None
    assert app.repair_generation_btn.winfo_manager()==''
    assert app.pending_revision is not None
    assert app.pending_revision['kind']=='new'
    assert app.history.head()['revisionId']==initial['revisionId']
    assert (app.manager.root/'outputs'/'latest-design.json').read_bytes()==outputs
    app.approve_draft_btn.invoke()
    _complete(app)
    assert app.history.head()['design']['title']==fixed['title']
    assert app.history.head()['worldWrites']==0
    assert app.history.head()['parentId'] is None


def test_second_b143_failure_never_hidden_retries(studio,monkeypatch):
    app,_=studio
    invalid=_draft('消防分署（座標不合格）')
    calls=[]
    monkeypatch.setattr(studio_main,'ask_llm',lambda *a,**k:(calls.append(k),copy.deepcopy(invalid))[1])
    def reject(engine,design):
        raise core.StudioError('B143_OUTSIDE_LOCAL_BOUNDS: y=24')
    monkeypatch.setattr(studio_main,'run_bridge',reject)
    head=app.history.head()['revisionId']
    app.generate(); _complete(app)
    assert len(calls)==2
    app.repair_generation_btn.invoke(); _complete(app)
    assert len(calls)==3
    assert app.failed_generation['design']==invalid
    assert app.history.head()['revisionId']==head
    assert app.repair_generation_btn.winfo_manager()=='pack'


def test_non_source_engine_error_is_not_repairable(studio,monkeypatch):
    app,_=studio
    calls=[];errors=[]
    monkeypatch.setattr(studio_main,'ask_llm',lambda *a,**k:(calls.append(1),_draft('new'))[1])
    monkeypatch.setattr(studio_main,'run_bridge',lambda *_:(_ for _ in ()).throw(core.StudioError('固定B143のハッシュ不一致')))
    monkeypatch.setattr(studio_main.messagebox,'showerror',lambda *args:errors.append(args))
    before=app.history.head()['revisionId']
    app.generate();_complete(app)
    assert len(calls)==1 and len(errors)==1
    assert app.failed_generation is None
    assert app.repair_generation_btn.winfo_manager()==''
    assert app.history.head()['revisionId']==before


def test_stale_approved_head_refuses_repair(studio,monkeypatch):
    app,_=studio
    app.auto_repair_invalid_generation = False  # Explicit fallback path
    invalid=_draft('座標不合格')
    calls=[];errors=[]
    monkeypatch.setattr(studio_main,'ask_llm',lambda *a,**k:(calls.append(k),copy.deepcopy(invalid))[1])
    monkeypatch.setattr(studio_main,'run_bridge',lambda *_:(_ for _ in ()).throw(core.StudioError('B143_OUTSIDE_LOCAL_BOUNDS')))
    monkeypatch.setattr(studio_main.messagebox,'showerror',lambda *a:errors.append(a))
    app.generate();_complete(app)
    assert len(calls)==1
    snapshot=app.history.head()
    app.history.commit(snapshot['design'],snapshot['scene'],engine_commit=app.manager.active_sha,
                       operation='new',expected_head_id=snapshot['revisionId'])
    app.repair_generation_btn.invoke()
    assert len(calls)==1 and errors
    assert app.failed_generation is None
    assert app.repair_generation_btn.winfo_manager()==''


def test_cancel_during_explicit_repair_keeps_failed_candidate(studio,monkeypatch):
    app,_=studio
    app.auto_repair_invalid_generation = False  # Explicit fallback path
    import threading
    app,_=studio
    invalid=_draft('失敗案')
    calls=[]
    entered=threading.Event();release=threading.Event()
    def model(*args,**kw):
        calls.append(kw)
        if len(calls)>1:
            entered.set(); release.wait(3)
        return copy.deepcopy(invalid)
    monkeypatch.setattr(studio_main,'ask_llm',model)
    monkeypatch.setattr(studio_main,'run_bridge',lambda *_:(_ for _ in ()).throw(core.StudioError('B143_OUTSIDE_LOCAL_BOUNDS')))
    before=app.history.head()['revisionId']
    app.generate();_complete(app)
    app.repair_generation_btn.invoke()
    assert entered.wait(2)
    app.update()
    app.cancel_btn.invoke()
    release.set()
    _complete(app)
    assert len(calls)==2
    assert app.failed_generation['design']==invalid
    assert app.history.head()['revisionId']==before
    assert 'キャンセル済み' in app.status.get()
    assert app.repair_generation_btn.winfo_manager()=='pack'


def test_new_generation_does_not_erase_repairable_failed_draft_on_model_error(studio, monkeypatch):
    app, _ = studio
    prior = {'design': _draft('修正待ちの原案'), 'error': 'B143_BOUNDS'}
    app.failed_generation = prior
    app._refresh_buttons()
    questions = []
    errors = []
    monkeypatch.setattr(studio_main.messagebox, 'askyesno',
                        lambda *a, **kw: questions.append(a[1]) or True)
    monkeypatch.setattr(studio_main.messagebox, 'showerror',
                        lambda *a, **kw: errors.append(a))
    monkeypatch.setattr(studio_main, 'ask_llm',
                        lambda *a, **kw: (_ for _ in ()).throw(core.StudioError('Ollama offline')))
    head = app.history.head()['revisionId']
    app.generate(); _complete(app)
    assert len(questions) == 1 and '修正待ち' in questions[0]
    assert errors
    assert app.failed_generation is prior
    assert app.history.head()['revisionId'] == head
    assert app.repair_generation_btn.winfo_manager() == 'pack'


def test_replacing_rejected_generation_requires_user_consent(studio, monkeypatch):
    app, _ = studio
    old = {'design': _draft('古い不合格案'), 'error': 'B143_BOUNDS'}
    app.failed_generation = old
    called = []
    monkeypatch.setattr(studio_main.messagebox, 'askyesno', lambda *a, **kw: False)
    monkeypatch.setattr(studio_main, 'ask_llm',
                        lambda *a, **kw: called.append(True) or _draft('new'))
    app.generate()
    assert not app.busy and not called and app.failed_generation is old
    app.show_sample()
    assert not app.busy and app.failed_generation is old
    assert app.history.head() is not None


def test_failed_source_is_kept_when_external_import_is_cancelled(studio, monkeypatch):
    app, _ = studio
    old = {'design': _draft('古い不合格案'), 'error': 'B143_BOUNDS'}
    app.failed_generation = old
    monkeypatch.setattr(studio_main.filedialog, 'askopenfilename',
                        lambda **kw: 'some-external-source.json')
    monkeypatch.setattr(studio_main.messagebox, 'askyesno', lambda *a, **kw: False)
    monkeypatch.setattr(studio_main, 'read_external_b143_source',
                        lambda *_: (_ for _ in ()).throw(AssertionError('should not load')))
    app.import_external_v11_source()
    assert not app.busy and app.failed_generation is old


def test_auto_repair_transport_failure_retains_original_without_committing(studio, monkeypatch):
    app, _ = studio
    invalid = _draft('自動修正前の保護対象')
    head = app.history.head()['revisionId']
    output = (app.manager.root/'outputs'/'latest-design.json').read_bytes()
    calls = []
    def model(*args, **kw):
        calls.append(kw)
        if len(calls) == 2:
            raise core.ModelTransportError('Ollama通信中断')
        return copy.deepcopy(invalid)
    monkeypatch.setattr(studio_main, 'ask_llm', model)
    monkeypatch.setattr(studio_main, 'run_bridge', lambda *_: (_ for _ in ()).throw(
        core.StudioError('B143_OUTSIDE_LOCAL_BOUNDS: roof')))
    app.generate(); _complete(app)
    assert len(calls) == 2
    assert app.failed_generation['design'] == invalid
    assert '自動修正は完了しませんでした' in app.failed_generation['error']
    assert app.history.head()['revisionId'] == head
    assert (app.manager.root/'outputs'/'latest-design.json').read_bytes() == output


def test_automatic_repair_cancellation_does_not_commit(studio, monkeypatch):
    import threading
    app, _ = studio
    invalid = _draft('キャンセルする不合格案')
    head = app.history.head()['revisionId']
    entered = threading.Event(); release = threading.Event()
    calls = []
    def model(*args, **kw):
        calls.append(kw)
        if len(calls) == 2:
            entered.set()
            release.wait(3)
        return copy.deepcopy(invalid)
    monkeypatch.setattr(studio_main, 'ask_llm', model)
    monkeypatch.setattr(studio_main, 'run_bridge', lambda *_: (_ for _ in ()).throw(
        core.StudioError('B143_OUTSIDE_LOCAL_BOUNDS: roof')))
    app.generate()
    assert entered.wait(2)
    app.update(); app.cancel_btn.invoke()
    release.set(); _complete(app)
    assert len(calls) == 2
    assert app.history.head()['revisionId'] == head
    assert app.pending_revision is None
    assert app.failed_generation is None
    assert 'キャンセル済み' in app.status.get()
