"""One-inference revision deltas: local doubles, no Minecraft or real LLM."""
import copy
import json

import pytest

from app import core
from tests.test_local_llm_flow import DESIGN
from tests.test_gui_review_flow import studio


def _revision_patch():
    element = copy.deepcopy(DESIGN['elements'][0])
    element['size'] = [8, 4, 8]  # Same footprint, one higher floor.
    return {'schema': core.REVISION_DELTA_SCHEMA_ID, 'replace':[element],
            'add':[], 'removeIds':[]}


def test_delta_replaces_only_target_and_never_mutates_approved_design():
    original = copy.deepcopy(DESIGN)
    extra = copy.deepcopy(original['elements'][0]);extra['id']='window';extra['material']='glass'
    original['elements'].append(extra)
    before = copy.deepcopy(original)
    result = core.apply_revision_delta(original, _revision_patch())
    assert original == before
    assert result['elements'][0]['size'] == [8, 4, 8]
    assert result['elements'][1] == extra
    assert result['bounds'] == before['bounds']
    assert result['id'] == before['id']
    assert result['designOnly'] is True
    assert result['schema'] == core.SCHEMA


def test_delta_add_remove_optional_bounds_only_from_model_not_auto_expansion():
    original = copy.deepcopy(DESIGN)
    patch = {'schema':core.REVISION_DELTA_SCHEMA_ID, 'replace': [], 'removeIds':['base'],
             'add':[{'id':'new','shape':'box','mode':'add', 'material':'metal',
                     'origin':[3,1,3],'size':[7,6,7]}],
             'bounds':{'width':18,'height':20,'depth':18}}
    result=core.apply_revision_delta(original,patch)
    assert result['bounds'] == patch['bounds']
    assert result['elements'] == patch['add']
    assert original == DESIGN


@pytest.mark.parametrize('bad', [
    {},
    {'schema':'minecraftaibuilder-b143-building-dsl-v1','replace':[],'add':[],'removeIds':[]},
    {'schema':core.REVISION_DELTA_SCHEMA_ID,'replace':[],'add':[],'removeIds':[]},
    {'schema':core.REVISION_DELTA_SCHEMA_ID,'replace':[],'add':[],'removeIds':['missing']},
    {'schema':core.REVISION_DELTA_SCHEMA_ID,'replace':[],'add':[],'removeIds':['base','base']},
    {'schema':core.REVISION_DELTA_SCHEMA_ID,'replace':[],
     'add':[{'id':'base','shape':'box','mode':'add','origin':[1,1,1],'size':[1,1,1]}],
     'removeIds':[]},
    {'schema':core.REVISION_DELTA_SCHEMA_ID,'replace':[],'add':[],'removeIds':[],'worldWrites':1},
    {'schema':core.REVISION_DELTA_SCHEMA_ID,'replace':[],'add':'bad','removeIds':[]},
])
def test_delta_rejects_invalid_or_empty_changes_without_side_effects(bad):
    original = copy.deepcopy(DESIGN)
    with pytest.raises(core.StudioError):
        core.apply_revision_delta(original, bad)
    assert original == DESIGN


@pytest.mark.parametrize('provider', ['LM Studio', 'Ollama'])
def test_revision_delta_sends_one_post_and_returns_complete_b143_source(monkeypatch,provider):
    posted=[]
    patch=_revision_patch()
    def fake_response(url, payload, timeout, on_progress, cancel):
        posted.append((url,payload))
        content=json.dumps(patch)
        if provider=='LM Studio':
            return {'choices':[{'finish_reason':'stop','message':{'content':content}}]}
        return {'done':True,'done_reason':'stop','message':{'content':content}}
    monkeypatch.setattr(core,'_model_response',fake_response)
    monkeypatch.setattr(core,'_output_budget',lambda *args:(4000,None))
    original=copy.deepcopy(DESIGN)
    revised=core.ask_llm(provider,'test-loaded','屋根を1マス高く',
                         revision_base=original,revision_mode='delta')
    assert len(posted)==1
    assert revised['elements'][0]['size']==[8,4,8]
    assert original==DESIGN
    assert revised['schema']==core.SCHEMA
    request=posted[0][1]
    assert 'mab-studio-b143-revision-delta-v1' in request['messages'][1]['content']
    assert '既存設計' in request['messages'][1]['content']
    schema=(request['response_format']['json_schema']['schema']
            if provider=='LM Studio' else request['format'])
    assert schema['properties']['schema']['const']==core.REVISION_DELTA_SCHEMA_ID
    assert 'elements' not in schema['properties']
    assert schema['properties']['replace']['maxItems']==core.MAX_DELTA_CHANGES
    assert not any('Minecraft' in name for name in schema['properties'])


def test_default_full_source_revision_path_still_available(monkeypatch):
    seen=[]
    def fake_response(url, payload, timeout, progress, cancel):
        seen.append(payload)
        return {'done':True,'done_reason':'stop',
                'message':{'content':json.dumps(DESIGN)}}
    monkeypatch.setattr(core,'_model_response',fake_response)
    monkeypatch.setattr(core,'_output_budget',lambda *args:(4000,None))
    assert core.ask_llm('Ollama','test-loaded','入口を変更',revision_base=DESIGN)==DESIGN
    assert 'elements' in seen[0]['format']['properties']


def test_patch_output_requires_full_verified_source_contract(tmp_path):
    from tests.test_core import fake_engine
    result=core.apply_revision_delta(DESIGN,_revision_patch())
    scene=core.run_bridge(fake_engine(tmp_path/'engine'),result)
    assert scene['stats']['voxels'] > 0
    assert scene['BuildPlan']['operations']==[]


def test_gui_defaults_to_delta_and_allows_full_compatibility_mode(studio, monkeypatch):
    from app import main
    from tests.test_gui_review_flow import _complete
    from app.examples import example_library
    selected=[]
    revised=copy.deepcopy(example_library())
    revised['title']='approved draft candidate'
    def stub(*args, **kwargs):
        selected.append(kwargs.get('revision_mode'))
        return copy.deepcopy(revised)
    monkeypatch.setattr(main,'ask_llm',stub)
    app,_=studio
    assert app.fast_revision_mode.get() is True
    app.revise_design();_complete(app)
    assert app.pending_revision is not None
    assert selected==['delta']
    app.reject_revision()
    app.fast_revision_mode.set(False)
    app.revise_design();_complete(app)
    assert app.pending_revision is not None
    assert selected==['delta','full']
