"""Actual Tkinter review controls; simulated local model, NO Minecraft/real AI."""
from __future__ import annotations
import copy
import hashlib
import os
from pathlib import Path
import threading
import time
import pytest
from app import core
from app.examples import example_library
from app import main as studio_main


def _scene(design, material='stone'):
    # Fixture respects RevisionStore's verified-source fields.
    digest = hashlib.sha256((design['title']+material).encode()).hexdigest()
    cells = [[x,1,z,0,0] for x in range(3,6) for z in range(3,5)]
    if material=='glass':
        cells[0][3]=1
    return {'title':design['title'], 'designOnly':True, 'readiness':'NOT_PREPARED',
            'worldContacts':0,'worldCoordinates':None,'BuildPlan':{'operations':[]},
            'stats':{'voxels':len(cells),'constructionApproved':False},
            'sourceSha256':digest,'geometrySha256':digest,'layerNames':['stone','glass'],
            'primitives':cells,'bounds':{'minX':3,'maxX':5,'minY':1,'maxY':1,'minZ':3,'maxZ':4}}


@pytest.fixture
def studio(tmp_path,monkeypatch):
    if os.name!='nt' and not os.getenv('DISPLAY'):
        pytest.skip('GUI test needs DISPLAY (use xvfb-run -a on Linux)')
    class Manager:
        root=tmp_path
        active_sha=core.BASELINE_SHA
        active_path=tmp_path/'fixed-engine'
        state={'tracked_branch':core.DEFAULT_BRANCH}
    monkeypatch.setattr(studio_main,'EngineManager',Manager)
    orig=example_library()
    revised=copy.deepcopy(orig)
    revised['title']='approved draft candidate'
    scenes={orig['title']:_scene(orig),revised['title']:_scene(revised,'glass')}
    calls=[]
    def fake_bridge(engine, design):
        calls.append(design['title'])
        return copy.deepcopy(scenes[design['title']])
    monkeypatch.setattr(studio_main,'run_bridge',fake_bridge)
    monkeypatch.setattr(studio_main,'ask_llm',lambda *a,**kw:copy.deepcopy(revised))
    root=studio_main.Studio()
    root.model.set('fixture-model')
    root._display_design(orig,scenes[orig['title']],'OFFLINE_SAMPLE','','original')
    try:
        yield root,calls
    finally:
        for pending in root.tk.call('after', 'info'):
            try: root.after_cancel(pending)
            except Exception: pass
        root.destroy()


def _complete(root,limit=3.0):
    end=time.monotonic()+limit
    while root.busy and time.monotonic()<end:
        root.update()
        time.sleep(.02)
    root.update()
    assert not root.busy,'worker hung during GUI review test'


def test_ai_draft_never_commits_until_explicit_approval(studio):
    app,calls=studio
    initial=app.history.head()
    original_src=(app.manager.root/'outputs'/'latest-design.json').read_bytes()
    app.revise_design()
    _complete(app)
    assert app.pending_revision is not None
    assert app.history.head()['revisionId']==initial['revisionId']
    assert (app.manager.root/'outputs'/'latest-design.json').read_bytes()==original_src
    assert app.view_mode.get()=='変更差分'
    assert 'disabled' not in app.approve_draft_btn.state()
    assert 'disabled' not in app.reject_draft_btn.state()
    # Approved source remains unchanged during candidate review.
    assert app.design['title']==initial['design']['title']
    app.approve_revision()
    _complete(app)
    assert calls.count('approved draft candidate')>=2
    assert app.pending_revision is None
    assert app.history.head()['parentId']==initial['revisionId']
    assert app.history.head()['design']['title']=='approved draft candidate'
    assert app.history.head()['worldWrites']==0
    assert 'disabled' in app.approve_draft_btn.state()


def test_reject_discards_candidate_without_touching_history(studio):
    app,_=studio
    start=app.history.head()['revisionId']
    app.revise_design()
    _complete(app)
    app.reject_revision()
    assert app.pending_revision is None
    assert app.history.head()['revisionId']==start
    assert app.view_mode.get()=='建築のみ'
    assert 'disabled' not in app.revise_btn.state()


def test_cancel_requested_revision_is_discarded_without_history_write(studio,monkeypatch):
    app,calls=studio
    before=app.history.head()
    output=(app.manager.root/'outputs'/'latest-design.json').read_bytes()
    entered=threading.Event();release=threading.Event()
    def slow_double(*args,**kwargs):
        entered.set()
        release.wait(2)
        return copy.deepcopy(example_library())
    monkeypatch.setattr(studio_main,'ask_llm',slow_double)
    app.prompt.insert('1.0','窓を修正')
    app.revise_design()
    assert entered.wait(1)
    app.update()
    assert 'disabled' not in app.cancel_btn.state()
    app.cancel_btn.invoke()
    assert 'サーバー側の推論停止は未確認' in app.status.get()
    release.set()
    _complete(app)
    assert 'キャンセル済み' in app.status.get()
    assert app.pending_revision is None
    assert app.history.head()['revisionId']==before['revisionId']
    assert (app.manager.root/'outputs'/'latest-design.json').read_bytes()==output
    assert calls.count('approved draft candidate')==0
    assert 'disabled' in app.cancel_btn.state()
    assert 'disabled' not in app.generate_btn.state()
    assert 'disabled' not in app.revise_btn.state()


def test_generate_cancel_at_completed_answer_boundary_discards_before_source(studio,monkeypatch):
    app,calls=studio
    before=app.history.head()
    output=(app.manager.root/'outputs'/'latest-design.json').read_bytes()
    def answer_then_cancel(*args,**kwargs):
        kwargs['cancel_event'].set()  # response completed just before GUI accepts it
        return copy.deepcopy(example_library())
    monkeypatch.setattr(studio_main,'ask_llm',answer_then_cancel)
    app.prompt.insert('1.0','未来的な建物を作る')
    app.generate()
    _complete(app)
    assert calls==[]  # cancelled before SOURCE verification begins
    assert app.history.head()['revisionId']==before['revisionId']
    assert (app.manager.root/'outputs'/'latest-design.json').read_bytes()==output
    assert app.current_snapshot['revisionId']==before['revisionId']
    assert app.pending_revision is None
    assert 'キャンセル済み' in app.status.get()
    assert 'disabled' in app.cancel_btn.state()
    assert 'disabled' not in app.generate_btn.state()


def test_generate_cancel_during_source_verification_discards_result(studio,monkeypatch):
    app,calls=studio
    before=app.history.head()
    output=(app.manager.root/'outputs'/'latest-design.json').read_bytes()
    entered=threading.Event();release=threading.Event()
    def slow_bridge(_engine,design):
        entered.set()
        release.wait(2)
        return _scene(design)
    monkeypatch.setattr(studio_main,'ask_llm',lambda *a,**kw:copy.deepcopy(example_library()))
    monkeypatch.setattr(studio_main,'run_bridge',slow_bridge)
    app.prompt.insert('1.0','未来的な建物を作る')
    app.generate()
    assert entered.wait(1)
    app.update()
    assert 'disabled' not in app.cancel_btn.state()
    app.cancel_btn.invoke()
    release.set()
    _complete(app)
    assert app.history.head()['revisionId']==before['revisionId']
    assert (app.manager.root/'outputs'/'latest-design.json').read_bytes()==output
    assert app.current_snapshot['revisionId']==before['revisionId']
    assert app.pending_revision is None
    assert 'キャンセル済み' in app.status.get()
    assert 'disabled' in app.cancel_btn.state()
    assert 'disabled' not in app.generate_btn.state()
    assert 'disabled' not in app.revise_btn.state()


def test_restart_restore_and_reopen_keeps_append_only_history(studio):
    app,_=studio
    original=app.history.head()
    app.revise_design();_complete(app)
    app.approve_revision();_complete(app)
    approved=app.history.head()
    assert approved['parentId']==original['revisionId']
    assert approved['sourceSha256']!=original['sourceSha256']
    assert approved['geometrySha256']!=original['geometrySha256']
    app.destroy()

    restarted=studio_main.Studio()
    restarted.model.set('fixture-model')
    try:
        items=restarted.history.list_saved()
        assert {item['revisionId'] for item in items}>={original['revisionId'],approved['revisionId']}
        restarted.load_saved_design();_complete(restarted)
        assert restarted.current_snapshot['revisionId']==approved['revisionId']
        assert restarted.scene['sourceSha256']==approved['sourceSha256']
        assert restarted.scene['geometrySha256']==approved['geometrySha256']
        restarted.restore_previous();_complete(restarted)
        restored=restarted.history.head()
        assert restored['operation']=='restore'
        assert restored['parentId']==approved['revisionId']
        assert restored['sourceSha256']==original['sourceSha256']
        assert restored['geometrySha256']==original['geometrySha256']
        restarted._open_saved_revision(approved['revisionId']);_complete(restarted)
        reopened=restarted.history.head()
        assert reopened['revisionId']==approved['revisionId']
        assert reopened['sourceSha256']==approved['sourceSha256']
        assert reopened['geometrySha256']==approved['geometrySha256']
        snapshots=restarted.history.list_saved()
        assert len(snapshots)==3
    finally:
        for pending in restarted.tk.call('after','info'):
            try:restarted.after_cancel(pending)
            except Exception:pass
        restarted.destroy()
