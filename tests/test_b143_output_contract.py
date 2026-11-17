"""B143-aware output grammar and private candidate diagnostics.

All model calls are test doubles. Real MAB module is authoritative when present.
"""
import copy
import json
import os
from pathlib import Path
import pytest
from app import core
from app.acceptance import inspect_candidate_shape_fields


def _valid_element(shape, mode):
    details = {
        'box': {'origin': [3, 3, 3], 'size': [6, 6, 6]},
        'ellipsoid': {'center': [9, 9, 9], 'radii': [4, 4, 4]},
        'cylinder': {'origin': [10, 3, 10], 'axis': 'y', 'radius': 3, 'length': 8},
    }
    d = {'id':'shape', 'shape':shape, 'mode':mode, **details[shape]}
    if mode == 'add':
        d['material'] = 'stone'
    return d


def _matches_grammar_fields(arm, candidate):
    # This helper tests only required/prohibited keys and shape/mode constants;
    # it is not a substitute for the MAB spatial/voxel compiler.
    return (set(arm['required']) <= set(candidate)
            and set(candidate) <= set(arm['properties'])
            and candidate.get('shape') == arm['properties']['shape']['const']
            and candidate.get('mode') == arm['properties']['mode']['const'])


def test_six_shape_mode_arms_are_unambiguous_and_not_spatial_compiler():
    arms = core.MODEL_GENERATION_SCHEMA['properties']['elements']['items']['anyOf']
    assert len(arms) == 6
    assert {(a['properties']['shape']['const'],a['properties']['mode']['const'])
            for a in arms} == {(s,m) for s in ('box','ellipsoid','cylinder')
                             for m in ('add','cut')}
    for shape in ('box','ellipsoid','cylinder'):
        for mode in ('add','cut'):
            d = _valid_element(shape,mode)
            assert sum(_matches_grammar_fields(a,d) for a in arms) == 1
            if mode == 'add':
                without_material = dict(d); without_material.pop('material')
                assert not any(_matches_grammar_fields(a,without_material) for a in arms)
            else:
                extra_material = {**d,'material':'stone'}
                assert not any(_matches_grammar_fields(a,extra_material) for a in arms)
                extra_shell = {**d,'shell':1}
                assert not any(_matches_grammar_fields(a,extra_shell) for a in arms)
            wrong = {**d,'strayCoordinate':999}
            assert not any(_matches_grammar_fields(a,wrong) for a in arms)


def test_revision_output_grammar_preserves_full_b143_limits():
    full=core._shape_specific_output_schema(max_elements=160,compact=False)
    assert full['properties']['elements']['maxItems']==160
    assert full['properties']['title']['maxLength']==100
    assert full['properties']['brief']['maxLength']==500
    assert all(a['properties']['id'].get('maxLength') is None
               for a in full['properties']['elements']['items']['anyOf'])


def test_failure_diagnostics_do_not_record_model_content_or_autofix():
    original={'title':'SECRET_REQUEST', 'elements':[
       {'id':'x','shape':'box','mode':'add','origin':[1,2,3],'size':[4,5,6]},
       {'id':'s','shape':'cylinder','mode':'cut','origin':[9,3,9],
        'radius':4,'length':3,'axis':'y','material':'PRIVATE_STONE'},
    ]}
    before=copy.deepcopy(original)
    result=inspect_candidate_shape_fields(original)
    assert result['elementCount']==2
    assert result['hints']==[
        {'index':0,'kind':'FIELD_CONTRACT_MISMATCH','missingKeys':['material'],'unexpectedKeyCount':0},
        {'index':1,'kind':'FIELD_CONTRACT_MISMATCH','missingKeys':[],'unexpectedKeyCount':1}
    ]
    assert original==before
    assert 'SECRET_REQUEST' not in json.dumps(result)
    assert 'PRIVATE_STONE' not in json.dumps(result)
    assert result['modelContentSaved'] is False


@pytest.mark.real_engine
def test_all_six_syntax_variants_compile_with_real_pinned_b143():
    path=os.getenv('MAB_B143_VERIFIED_ENGINE')
    if not path:
        pytest.skip('Requires actual reviewed engine')
    for shape in ('box','ellipsoid','cylinder'):
        for mode in ('add','cut'):
            elems=[{'id':'base','shape':'box','mode':'add','material':'glass',
                    'origin':[1,1,1],'size':[16,16,16]}]
            element=_valid_element(shape,mode)
            if mode=='add':
                element['id']='additional'
            elems.append(element)
            design={'schema':core.SCHEMA,'id':'variant-safety-test',
                    'title':'Test','brief':'Shape syntax only', 'designOnly':True,
                    'bounds':{'width':20,'height':20,'depth':20},'elements':elems}
            scene=core.run_bridge(Path(path),design)
            assert scene['BuildPlan']['operations']==[]
            assert scene['worldContacts']==0
            assert scene['readiness']=='NOT_PREPARED'
