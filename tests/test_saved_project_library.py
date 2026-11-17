"""Real saved project files and recovery. Source-only; fake compiler is marked."""
import copy
import json
import pytest
from app import core
from app.examples import example_library
from app.revisions import RevisionStore
from tests.test_gui_review_flow import _scene


def test_open_older_project_without_deleting_any_revision(tmp_path):
    store=RevisionStore(tmp_path/'projects')
    first=example_library()
    second=copy.deepcopy(first)
    second['title']='Second library'
    one=store.commit(first,_scene(first),engine_commit=core.BASELINE_SHA)
    two=store.commit(second,_scene(second),engine_commit=core.BASELINE_SHA)
    original_files=set((tmp_path/'projects'/'revisions').glob('*.json'))
    rows=store.list_saved()
    assert {i['revisionId'] for i in rows}=={one['revisionId'],two['revisionId']}
    assert len({i['projectId'] for i in rows})==2
    opened=store.open_saved(one['revisionId'],expected_head_id=two['revisionId'],
                            verify=lambda doc:_scene(doc))
    assert opened['design']['title']==first['title']
    assert store.head()['revisionId']==one['revisionId']
    assert set((tmp_path/'projects'/'revisions').glob('*.json'))==original_files
    assert opened['worldWrites']==0
    assert not (tmp_path/'minecraftWorlds').exists()
    assert store.list_saved()[0]['isCurrent'] is False  # sort order != current head
    assert sum(row['isCurrent'] for row in store.list_saved())==1


def test_reject_mismatched_and_stale_saved_selection(tmp_path):
    store=RevisionStore(tmp_path/'projects')
    doc=example_library()
    record=store.commit(doc,_scene(doc),engine_commit=core.BASELINE_SHA)
    rid=record['revisionId']
    with pytest.raises(core.StudioError,match='再検証'):
        store.open_saved(rid,expected_head_id=rid)
    with pytest.raises(core.StudioError,match='更新競合'):
        store.open_saved(rid,expected_head_id=None,verify=lambda d:_scene(d))
    with pytest.raises(core.StudioError,match='SOURCE'):
        store.open_saved(rid,expected_head_id=rid,
                         verify=lambda d:{**_scene(d),'geometrySha256':'0'*64})
    assert store.head()['revisionId']==rid
    assert len(store.list_saved())==1


def test_tampered_record_never_appears_as_selectable(tmp_path):
    store=RevisionStore(tmp_path/'projects')
    doc=example_library()
    old=store.commit(doc,_scene(doc),engine_commit=core.BASELINE_SHA)
    badfile=tmp_path/'projects'/'revisions'/(old['revisionId']+'.json')
    bad=json.loads(badfile.read_text(encoding='utf-8'))
    bad['design']['title']='tampered'
    badfile.write_text(json.dumps(bad),encoding='utf-8')
    assert store.list_saved()==[]
    with pytest.raises(core.StudioError,match='改変'):
        store.open_saved(old['revisionId'],expected_head_id=old['revisionId'],
                         verify=lambda d:_scene(d))
    with pytest.raises(core.StudioError,match='改変'):
        store.head()
