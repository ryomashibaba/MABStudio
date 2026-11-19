"""Explicit recovery from a genuine B143 SOURCE refusal, never hidden retries.

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


def test_failed_generation_repairs_only_after_user_click(studio,monkeypatch):
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
    assert len(model_calls)==1
    assert app.history.head()['revisionId']==initial['revisionId']
    assert app.current_snapshot['revisionId']==initial['revisionId']
    assert (app.manager.root/'outputs'/'latest-design.json').read_bytes()==outputs
    assert app.failed_generation['design']['title']==invalid['title']
    assert app.repair_generation_btn.winfo_manager()=='pack'
    assert '不合格' in app.caption.get()
    assert 'disabled' not in app.repair_generation_btn.state()
    app.repair_generation_btn.invoke()
    _complete(app)
    assert len(model_calls)==2
    assert model_calls[1]['previous_design']==invalid
    assert 'B143_OUTSIDE_LOCAL_BOUNDS' in model_calls[1]['error']
    assert 'revision_base' not in model_calls[1]  # Repair invalid new design; no approved revision base.
    assert app.failed_generation is None
    assert app.repair_generation_btn.winfo_manager()==''
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
    assert len(calls)==1
    app.repair_generation_btn.invoke(); _complete(app)
    assert len(calls)==2
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
