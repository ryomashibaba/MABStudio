"""B143 bound-error explanation and revision UX; no real inference or world writes."""
import copy
import hashlib
import os
import shutil
import time

import pytest

from app import core
from tests.test_core import fake_engine


def _design(element):
    return {'schema': core.SCHEMA, 'id': 'revision-test', 'title': 'Tower',
            'brief': 'test', 'designOnly': True,
            'bounds': {'width': 12, 'height': 16, 'depth': 12},
            'elements': [element]}


@pytest.mark.parametrize(('element', 'expected'), [
    ({'id': 'top', 'shape': 'box', 'origin': [2, 12, 2], 'size': [8, 7, 8]},
     'Y=12..18（許可0..15）'),
    ({'id': 'glass', 'shape': 'ellipsoid', 'center': [2, 8, 3], 'radii': [4, 3, 4]},
     'X=-2..6（許可0..11）'),
    ({'id': 'tower', 'shape': 'cylinder', 'origin': [3, 2, 8], 'axis': 'y',
      'radius': 5, 'length': 12},
     'X=-2..8（許可0..11）'),
    ({'id': 'okay', 'shape': 'box', 'origin': [1, 1, 1], 'size': [10, 10, 10]}, None),
])
def test_bounds_diagnostic_is_precise_read_only(element, expected):
    source = _design(element)
    original = copy.deepcopy(source)
    detail = core.explain_b143_local_bounds(source)
    assert source == original  # absolutely no geometry or bounds repair
    if expected is None:
        assert detail is None
    else:
        assert expected in detail
        assert element['id'] in detail
        assert '承認済み建築と履歴は変更されません' in detail


def test_bounds_diagnostic_avoids_claiming_bad_json_or_unrelated_errors():
    assert core.explain_b143_local_bounds({'bounds':{},'elements':[]}) is None
    assert core.explain_b143_local_bounds({'bounds':{'width':False,'height':16,'depth':12},'elements':[]}) is None
    assert core.explain_b143_local_bounds('bad') is None


@pytest.mark.skipif(not shutil.which('node'), reason='Node.js is required')
def test_bridge_shows_offending_element_only_after_engine_rejects(tmp_path, monkeypatch):
    root = fake_engine(tmp_path/'engine')
    original_module = (root / core.ENGINE_FILE).read_text()
    reject = original_module.replace(
        "if (d?.schema !== SCHEMA || d?.designOnly !== true) throw Error('BAD_SCHEMA');",
        "if (d?.elements?.[0]?.size?.[1] > 5) throw Error('B143_OUTSIDE_LOCAL_BOUNDS');"
        "if (d?.schema !== SCHEMA || d?.designOnly !== true) throw Error('BAD_SCHEMA');")
    assert reject != original_module
    (root / core.ENGINE_FILE).write_text(reject)
    monkeypatch.setattr(core, 'APPROVED_B143_SOURCE_SHA256', hashlib.sha256(reject.encode()).hexdigest())
    with pytest.raises(core.StudioError) as exc:
        core.run_bridge(root, _design({'id': 'top', 'shape':'box', 'mode':'add',
                                       'material':'stone', 'origin':[2,12,2], 'size':[8,7,8]}))
    assert 'B143_OUTSIDE_LOCAL_BOUNDS' in str(exc.value)
    assert 'Y=12..18（許可0..15）' in str(exc.value)


def test_revision_prompt_states_exact_shape_envelope_and_not_retry(monkeypatch):
    seen = []
    src = _design({'id':'main', 'shape':'box','mode':'add', 'material':'stone',
                   'origin':[1,1,1], 'size':[10,10,10]})
    def mock_response(url, payload, timeout, on_progress, cancel):
        seen.append(payload)
        return {'message':{'content': __import__('json').dumps(src)},
                'done':True, 'done_reason':'stop'}
    monkeypatch.setattr(core, '_model_response', mock_response)
    monkeypatch.setattr(core, '_output_budget', lambda *a:(4000,8192))
    assert core.ask_llm('Ollama','test-model','屋根を高く',revision_base=src) == src
    assert len(seen) == 1
    prompt = seen[0]['messages'][1]['content']
    assert 'X=12, Y=16, Z=12' in prompt
    assert 'origin+size-1' in prompt
    assert 'center±radii' in prompt
    assert 'origin±radius' in prompt
    assert '既存設計' in prompt


@pytest.mark.skipif(os.name != 'nt' and not os.getenv('DISPLAY'), reason='Tk needs DISPLAY')
def test_gui_failed_revision_keeps_approved_snapshot_and_one_model_call(studio_bounds_fixture, monkeypatch):
    app, calls = studio_bounds_fixture
    original = app.current_snapshot['revisionId']
    source = (app.manager.root/'outputs'/'latest-design.json').read_bytes()
    main_bridge = __import__('app.main', fromlist=['run_bridge'])
    previous_bridge = main_bridge.run_bridge
    original_title = app.design['title']
    infer_calls = []
    def fake_ask(*args, **kwargs):
        infer_calls.append(True)
        return _design({'id':'top', 'shape':'box', 'origin':[1,14,1], 'size':[8,7,8]})
    def fake_bridge(engine, design):
        if design.get('title') == 'Tower':
            raise core.StudioError('B143_OUTSIDE_LOCAL_BOUNDS: 要素「top」 Y=14..20')
        return previous_bridge(engine, design)
    monkeypatch.setattr(main_bridge, 'ask_llm', fake_ask)
    monkeypatch.setattr(main_bridge, 'run_bridge', fake_bridge)
    # A no-modal test expects the user-visible failure in the status.
    messages=[]
    monkeypatch.setattr(main_bridge.messagebox, 'showerror', lambda title, msg: messages.append((title,msg)))
    app.prompt.delete('1.0','end'); app.prompt.insert('1.0','もっと高くする')
    app.revise_design()
    stop=time.monotonic()+4
    while app.busy and time.monotonic()<stop:
        app.update();time.sleep(.02)
    app.update()
    assert not app.busy
    assert len(infer_calls)==1
    assert len(messages)==1
    assert 'B143_OUTSIDE_LOCAL_BOUNDS' in messages[0][1]
    assert 'AI応答' in messages[0][1]
    assert app.current_snapshot['revisionId']==original
    assert app.design['title']==original_title
    assert app.pending_revision is None
    assert (app.manager.root/'outputs'/'latest-design.json').read_bytes()==source


@pytest.fixture
def studio_bounds_fixture(tmp_path, monkeypatch):
    # Reuse the existing proven real-Tk test fixture as an underlying fixture.
    from tests.test_gui_review_flow import _scene
    from app import main
    from app.examples import example_library
    class Manager:
        root=tmp_path
        active_sha=core.BASELINE_SHA
        active_path=tmp_path/'fixed-engine'
        state={'tracked_branch': core.DEFAULT_BRANCH}
    monkeypatch.setattr(main,'EngineManager', Manager)
    design=example_library()
    monkeypatch.setattr(main,'run_bridge', lambda e,d: _scene(d))
    monkeypatch.setattr(main,'verify_loaded_model',lambda provider,model:{'inferenceModel':model,'loaded':True})
    app=main.Studio()
    app.model.set('fixture')
    app._display_design(design,_scene(design),'TEST','','original')
    try:
        yield app, []
    finally:
        for after_id in app.tk.call('after','info'):
            try: app.after_cancel(after_id)
            except Exception: pass
        app.destroy()
