"""User-facing saved project/export continuity; no model inference or world writes."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import pytest
from app import main as studio_main
from app.project_io import export_verified_source
from app.core import SCHEMA, StudioError
from app.examples import example_library
from tests.test_gui_review_flow import studio, _scene, _complete


def test_export_exact_current_source_refuses_existing_or_stale(tmp_path):
    design=example_library()
    scene=_scene(design)
    target=tmp_path/'test-source.json'
    def compiled(doc):
        assert doc==design
        return copy.deepcopy(scene)
    assert export_verified_source(target,design,scene['sourceSha256'],scene['geometrySha256'],compiled)==target
    assert json.loads(target.read_text(encoding='utf-8'))==design
    saved=target.read_bytes()
    with pytest.raises(StudioError,match='別のファイル名'):
        export_verified_source(target,design,scene['sourceSha256'],scene['geometrySha256'],compiled)
    assert target.read_bytes()==saved
    with pytest.raises(StudioError,match='一致しません'):
        export_verified_source(tmp_path/'different.json',design,'0'*64,scene['geometrySha256'],compiled)
    with pytest.raises(StudioError,match='json'):
        export_verified_source(tmp_path/'not-a-world.mcworld',design,scene['sourceSha256'],scene['geometrySha256'],compiled)
    assert sorted(p.name for p in tmp_path.iterdir())==['test-source.json']


def test_export_button_saves_active_approved_design_not_old_latest_file(studio,monkeypatch,tmp_path):
    app,_=studio
    output=tmp_path/'selected-source.json'
    notices=[]
    dialogs=[]
    monkeypatch.setattr(studio_main.filedialog,'asksaveasfilename',lambda **kw:(dialogs.append(kw) or str(output)))
    monkeypatch.setattr(studio_main.messagebox,'showinfo',lambda *a: notices.append(a))
    assert app.export_btn.cget('text')=='SOURCE JSONを書き出す'
    app.export_design()
    _complete(app)
    assert output.is_file()
    assert json.loads(output.read_text(encoding='utf-8'))==app.design
    assert notices
    assert 'アプリ内履歴とは別' in dialogs[0]['title']
    assert '建築履歴とは別のファイル' in notices[0][1]
    assert '建築履歴に自動保存' in notices[0][1]
    assert app.history.head()['worldWrites']==0


def test_switching_to_older_project_syncs_legacy_outputs(studio):
    app,_=studio
    original=app.history.head()
    second=copy.deepcopy(original['design'])
    second['title']='second building'
    second_scene=_scene(second)
    app._display_design(second,second_scene,'OFFLINE_SAMPLE','','new building')
    assert json.loads((app.manager.root/'outputs'/'latest-design.json').read_text(encoding='utf-8'))['title']=='second building'
    app._open_saved_revision(original['revisionId'])
    _complete(app)
    assert app.current_snapshot['revisionId']==original['revisionId']
    assert app.design==original['design']
    output=app.manager.root/'outputs'
    assert json.loads((output/'latest-design.json').read_text(encoding='utf-8'))==original['design']
    assert json.loads((output/'latest-scene.json').read_text(encoding='utf-8'))['geometrySha256']==original['scene']['geometrySha256']
    assert json.loads((output/'latest-receipt.json').read_text(encoding='utf-8'))['worldWrites']==0


def test_auto_reopen_existing_history_does_not_add_revision(studio,monkeypatch):
    app,_=studio
    before=app.history.head()
    app._invalidate_scene()  # emulate freshly initialized view, keep immutable store
    # Simulate an old/stale autosaved preview; startup must correct it from
    # the independently SOURCE-verified head without making a new revision.
    outputs=app.manager.root/'outputs'
    (outputs/'latest-design.json').write_text('{"outdated": true}',encoding='utf-8')
    monkeypatch.setattr(app,'refresh_models',lambda:None)  # no network/inference
    app._restore_last_session()
    _complete(app)
    assert app.current_snapshot['revisionId']==before['revisionId']
    assert app.design==before['design']
    assert app.scene['geometrySha256']==before['scene']['geometrySha256']
    assert app.history.head()['revisionId']==before['revisionId']
    assert json.loads((outputs/'latest-design.json').read_text(encoding='utf-8'))==before['design']
    assert 'SOURCE再検証' in app.caption.get()


def test_auto_reopen_refuses_corrupt_old_source_without_resetting_it(studio,monkeypatch):
    app,_=studio
    old=app.history.head()
    source_file=app.history.folder/'revisions'/f'{old["revisionId"]}.json'
    source_file.write_text(source_file.read_text(encoding='utf-8').replace('"snapshotSha256"', '"badSnapshotHash"'), encoding='utf-8')
    app._invalidate_scene()
    monkeypatch.setattr(app,'refresh_models',lambda:None)
    app._restore_last_session()
    _complete(app)
    assert app.scene is None and app.design is None
    assert '安全に開けませんでした' in app.caption.get()
    assert source_file.is_file()
    # The corrupted source is never presented as an approved model.
    with pytest.raises(StudioError,match='改変'):
        app.history.head()


def test_unapproved_ai_draft_cannot_be_exported(studio,monkeypatch,tmp_path):
    app,_=studio
    notices=[]
    monkeypatch.setattr(studio_main.messagebox,'showwarning',lambda *args:notices.append(args))
    monkeypatch.setattr(studio_main.filedialog,'asksaveasfilename',lambda **kw:(_ for _ in ()).throw(AssertionError('must not open save dialog')))
    app.revise_design()
    _complete(app)
    assert app.pending_revision is not None
    app.export_design()
    assert notices and app.history.head()['worldWrites']==0
    app.reject_revision()
