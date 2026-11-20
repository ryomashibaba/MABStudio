"""Large-source delta context must be compact without changing SOURCE or safety gates."""
import copy
import json
import os
from pathlib import Path

import pytest

from app import core
from tests.test_local_llm_flow import DESIGN


def _large():
    original = copy.deepcopy(DESIGN)
    original['elements'] = [dict(original['elements'][0], id=f'part_{i}')
                            for i in range(160)]
    original['elements'][1] = {'id': 'glass_cut', 'shape': 'box', 'mode': 'cut',
                               'origin': [2, 1, 2], 'size': [1, 1, 1]}
    original['elements'][2] = {'id': 'round', 'shape': 'ellipsoid', 'mode': 'add',
                               'material': 'glass', 'center': [5, 5, 5],
                               'radii': [2, 2, 2], 'shell': 1}
    original['elements'][3] = {'id': 'beam', 'shape': 'cylinder', 'mode': 'add',
                               'material': 'wood', 'origin': [5, 5, 5],
                               'axis': 'y', 'radius': 2, 'length': 4}
    return original


def test_large_reference_keeps_all_source_element_ids_shapes_and_order():
    original = _large()
    untouched = copy.deepcopy(original)
    compact = core._compact_revision_reference(original)
    full = json.dumps(original, ensure_ascii=False, separators=(',', ':'))
    assert compact != full
    assert len(compact) < len(full) * 0.72
    rows = compact.splitlines()[4:]
    assert len(rows) == 160
    assert [r.split('|', 1)[0] for r in rows] == [e['id'] for e in original['elements']]
    assert 'glass_cut|b|-|_|2,1,2|1,1,1' in rows
    assert 'round|e|+|glass|5,5,5|2,2,2|shell=1' in rows
    assert 'beam|c|+|wood|5,5,5|y|2|4' in rows
    assert original == untouched


def test_unknown_key_falls_back_to_lossless_full_source_not_silent_omission():
    original = _large()
    original['elements'][40]['unsupported'] = 123
    assert core._compact_revision_reference(original) == json.dumps(
        original, ensure_ascii=False, separators=(',', ':'))


def test_small_existing_revision_format_not_changed():
    original = copy.deepcopy(DESIGN)
    assert core._compact_revision_reference(original) == json.dumps(
        original, ensure_ascii=False, separators=(',', ':'))


@pytest.mark.real_engine
def test_loaded_eight_k_lm_can_send_large_source_as_one_delta_request(monkeypatch):
    original = _large()
    before = copy.deepcopy(original)
    changed = copy.deepcopy(original['elements'][0])
    changed['material'] = 'glass'
    delta = {'schema': core.REVISION_DELTA_SCHEMA_ID, 'replace': [changed],
             'add': [], 'removeIds': []}
    sent = []
    def post(url, payload, timeout, progress, cancelled):
        sent.append(payload)
        return {'choices': [{'finish_reason': 'stop',
                             'message': {'content': json.dumps(delta)}}]}
    monkeypatch.setattr(core, '_model_response', post)
    monkeypatch.setattr(core, '_loaded_lm_studio_context', lambda model: 8192)
    result = core.ask_llm('LM Studio', 'loaded-example', 'part_0の材質をガラスへ',
                          revision_base=original, revision_mode='delta')
    assert len(sent) == 1
    assert sent[0]['max_tokens'] >= 800
    assert len(sent[0]['messages'][1]['content']) < len(json.dumps(
        original, ensure_ascii=False, separators=(',', ':')))
    assert len(result['elements']) == 160
    assert result['elements'][0]['material'] == 'glass'
    assert result['elements'][1:] == before['elements'][1:]
    assert before == original
    # The actual geometry authority remains the unchanged fixed B143 engine.
    folder = os.getenv('MAB_B143_VERIFIED_ENGINE')
    if folder:
        scene = core.run_bridge(Path(folder), result)
        assert scene['readiness'] == 'NOT_PREPARED'
        assert scene['worldContacts'] == 0
        assert scene['BuildPlan']['operations'] == []
