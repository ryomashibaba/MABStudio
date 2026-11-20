"""Bounded, geometry-preserving rescue for SOURCE drafts with undersized canvas.

This is not a visual-quality test or a replacement for pinned B143 verification.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import time
import pytest
from app import core, main as studio_main
from app.examples import example_library
from tests.test_gui_review_flow import studio as studio, _scene


def _outside():
    d = copy.deepcopy(example_library())
    d['bounds']['width'] = 16  # wall x=2..17; only the canvas is too small
    return d


def test_only_expands_needed_axes_and_leaves_elements_identical():
    invalid = _outside()
    original = copy.deepcopy(invalid)
    updated, receipt = core.expand_generated_canvas_bounds(invalid)
    assert updated['bounds'] == {'width': 18, 'height': 16, 'depth': 20}
    assert receipt['changes'] == {'width': {'before': 16, 'after': 18}}
    assert updated['elements'] == invalid['elements']
    assert invalid == original
    assert receipt['worldWrites'] == 0
    assert core.expand_generated_canvas_bounds(updated) is None


@pytest.mark.parametrize('changes', [
    {'elements': [{'id':'bad', 'shape':'ellipsoid', 'mode':'add', 'material':'glass',
                   'center':[1,3,3], 'radii':[2,2,2]}]}, # negative coordinates
    {'bounds': {'width': 80, 'height': 96, 'depth': 80}}, # 600k volume
    {'elements': [{'id':'wrong','shape':'staircase', 'mode':'add', 'material':'stone'}]},
    {'elements': [{'id':'repeat','shape':'box', 'mode':'add', 'material':'stone',
                   'origin':[1,1,1], 'size':[4,4,4],
                   'repeat': {'count':2,'offset':[4,0,0]}}]},
    {'designOnly': False},
])
def test_never_guesses_or_weakens_invalid_source(changes):
    invalid = {**_outside(), **changes}
    before = copy.deepcopy(invalid)
    assert core.expand_generated_canvas_bounds(invalid) is None
    assert invalid == before


def test_cylinder_and_ellipsoid_domain_endpoints():
    invalid = copy.deepcopy(example_library())
    invalid['bounds'] = {'width': 20, 'height': 16, 'depth': 20}
    invalid['elements'] = [
        {'id': 'cyl', 'shape':'cylinder', 'mode':'add', 'material':'stone',
         'origin':[14,6,12], 'axis':'x', 'radius': 4, 'length': 9},
        {'id': 'dome', 'shape':'ellipsoid', 'mode':'add', 'material':'glass',
         'center':[11,12,11], 'radii':[5,6,5]},
    ]
    result = core.expand_generated_canvas_bounds(invalid)
    assert result is not None
    assert result[0]['bounds'] == {'width': 23, 'height': 19, 'depth': 20}
    assert result[0]['elements'] == invalid['elements']


@pytest.mark.real_engine
def test_frozen_b143_is_required_to_accept_canvas_fix():
    root = os.getenv('MAB_B143_VERIFIED_ENGINE')
    if not root:
        pytest.skip('Frozen B143 engine not supplied')
    invalid = _outside()
    with pytest.raises(core.StudioError, match='B143_OUTSIDE_LOCAL_BOUNDS'):
        core.run_bridge(Path(root), invalid)
    candidate, _ = core.expand_generated_canvas_bounds(invalid)
    scene = core.run_bridge(Path(root), candidate)
    expected = copy.deepcopy(example_library())
    reference = core.run_bridge(Path(root), expected)
    assert scene['primitives'] == reference['primitives']
    assert scene['worldContacts'] == 0
    assert scene['BuildPlan']['operations'] == []
    assert scene['stats']['constructionApproved'] is False


def _complete(app):
    deadline=time.monotonic()+4
    while app.busy and time.monotonic()<deadline:
        app.update()
        time.sleep(.015)
    app.update()
    assert not app.busy


def test_one_model_request_yields_reviewable_draft_without_save(studio, monkeypatch):
    app, _ = studio
    candidate = _outside()
    saved = app.history.head()
    original_output = (app.manager.root/'outputs'/'latest-design.json').read_bytes()
    calls = []
    def fake_model(*args, **kw):
        calls.append(kw)
        return copy.deepcopy(candidate)
    def fake_bridge(engine, design):
        if design['bounds']['width'] < 18:
            raise core.StudioError('B143_OUTSIDE_LOCAL_BOUNDS')
        return _scene(design)
    monkeypatch.setattr(studio_main, 'ask_llm', fake_model)
    monkeypatch.setattr(studio_main, 'run_bridge', fake_bridge)
    app.generate()
    _complete(app)
    assert len(calls) == 1
    assert app.pending_revision is not None
    assert app.pending_revision['design']['bounds']['width'] == 18
    assert app.pending_revision['design']['elements'] == candidate['elements']
    assert app.history.head()['revisionId'] == saved['revisionId']
    assert (app.manager.root/'outputs'/'latest-design.json').read_bytes() == original_output
    assert app.failed_generation is None
    assert '建築範囲のみ自動補正' in app.status.get()
