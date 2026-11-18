"""Actual 2026-11-17 Gemma: complete JSON, B143_TITLE_OR_BRIEF refusal.

No real model inference. This tests deterministic metadata repair and the
independent PINNED B143 compile path, without changing geometry or worlds.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import pytest
from app import core
from tests.test_local_llm_flow import DESIGN


@pytest.mark.parametrize('changes', [
    {'title': '観測所\n展望ドーム', 'brief': '窓<多め>\t海沿い'},
    {'title': '', 'brief': None},
    {'title': None, 'brief': []},
    {'title': '天文台'*70, 'brief': '説明'*300},
    {'title': '🌌'*60, 'brief': '説明 '},
])
def test_metadata_repair_is_bounded_and_source_geometry_unchanged(changes):
    original = {**DESIGN, **changes}
    before_elements = json.dumps(original['elements'], ensure_ascii=False)
    repaired, receipt = core.repair_generated_metadata(original, '海辺の天文観測所を建てて')
    assert original['elements'] == repaired['elements']
    assert json.dumps(repaired['elements'], ensure_ascii=False) == before_elements
    assert core._valid_b143_metadata(repaired['title'], 100)
    assert core._valid_b143_metadata(repaired['brief'], 500)
    assert receipt['geometryUnchanged'] is True
    assert receipt['worldWrites'] == 0
    assert '海辺の天文観測所を建てて' not in str(receipt)
    assert original['title'] == changes['title']  # caller's original not modified


def test_do_not_touch_valid_model_metadata():
    design = dict(DESIGN)
    repaired, receipt = core.repair_generated_metadata(design, '好きな建築')
    assert repaired == design
    assert receipt['fields'] == {}


def test_revision_uses_approved_labels_not_user_instruction():
    design = {**DESIGN, 'title': [], 'brief': ''}
    result, evidence = core.repair_generated_metadata(design, '屋根を直す', revision_base=DESIGN)
    assert result['title'] == DESIGN['title']
    assert result['brief'] == DESIGN['brief']
    assert all('restored_from_approved_source' in action
               for action in evidence['fields'].values())


def test_model_request_uses_one_inference_and_reports_metadata_only(monkeypatch):
    wrong = {**DESIGN, 'title': 'A\nB', 'brief': '<invalid>'}
    calls = []
    receipts = []
    def fake_response(url, payload, timeout, on_progress=None, cancel_event=None):
        calls.append(payload)
        return {'choices': [{'message': {'content': json.dumps(wrong, ensure_ascii=False)},
                             'finish_reason': 'stop'}]}
    monkeypatch.setattr(core, '_model_response', fake_response)
    monkeypatch.setattr(core, '_loaded_lm_studio_context', lambda model: None)
    result = core.ask_llm('LM Studio', 'gemma-4-26b-a4b-it-qat', '4階建て',
                          on_metadata=receipts.append)
    assert len(calls) == 1
    assert len(receipts) == 1
    assert result['title'] == 'A B'
    assert result['brief'] == '＜invalid＞'
    assert result['elements'] == DESIGN['elements']
    assert receipts[0]['fields'].keys() == {'title', 'brief'}
    assert receipts[0]['originalIssues'] == {'title': 'FORBIDDEN_CHARACTERS', 'brief': 'FORBIDDEN_CHARACTERS'}


def test_repair_does_not_launder_geometry_or_unsafe_mode():
    design = {**DESIGN, 'title': '\nname', 'bounds': {'width': 0, 'height': 0, 'depth': 0},
              'designOnly': False}
    repaired, _ = core.repair_generated_metadata(design, '建築')
    assert repaired['bounds'] == design['bounds']
    assert repaired['designOnly'] is False


@pytest.mark.real_engine
def test_pinned_b143_accepts_metadata_repair_but_rejects_bad_geometry():
    path = os.getenv('MAB_B143_VERIFIED_ENGINE')
    if not path:
        pytest.skip('Needs pinned B143 engine')
    engine = Path(path)
    original = {**DESIGN, 'title': '', 'brief': '窓\n大きめ'}
    with pytest.raises(core.StudioError, match='B143_TITLE_OR_BRIEF'):
        core.run_bridge(engine, original)
    repaired, _ = core.repair_generated_metadata(original, '海沿いに図書館をつくる')
    scene = core.run_bridge(engine, repaired)
    baseline_scene = core.run_bridge(engine, DESIGN)
    assert scene['primitives'] == baseline_scene['primitives']
    assert scene['BuildPlan']['operations'] == []
    assert scene['worldContacts'] == 0
    malformed = {**repaired, 'bounds': {'width': 1, 'height': 10, 'depth': 10}}
    with pytest.raises(core.StudioError):
        core.run_bridge(engine, malformed)
