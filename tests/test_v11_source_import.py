"""Offline source-only V11 handoff: no model POST or Minecraft writes."""
import copy
import json

import pytest

from app.core import StudioError
from app.examples import example_library
from app.v11_source_import import MAX_SOURCE_JSON_BYTES, read_external_b143_source
from app import main as studio_main
from tests.test_gui_review_flow import studio, _complete, _scene


def _file(tmp_path, value, name='B143_DESIGN.json'):
    target = tmp_path / name
    target.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return target



def _product(design):
    # V11 TRUE_B143_PRODUCT.json structure, but all compiled data is ignored.
    return {'design': design, 'compiled': {'primitives': [[999, 999, 999, 9]]},
            'worldWrites': 0, 'worldContacts': 0, 'readiness': 'NOT_PREPARED',
            'executionAllowed': False, 'constructionApproved': False,
            'gates': ['BLOCKED'] * 9, 'BuildPlan': {'operations': []}}


def test_accepts_safe_v11_product_without_trusting_compiled_scene(tmp_path):
    original = example_library()
    wrapper = _product(copy.deepcopy(original))
    path = _file(tmp_path, wrapper, name='TRUE_B143_PRODUCT.json')
    raw = path.read_bytes()
    result = read_external_b143_source(path)
    assert result == original
    assert 'compiled' not in result
    assert path.read_bytes() == raw


@pytest.mark.parametrize('field,value', [
    ('worldWrites', 1), ('worldContacts', 1), ('readiness', 'READY'),
    ('executionAllowed', True), ('constructionApproved', True),
    ('gates', ['BLOCKED'] * 8 + ['PASS']),
    ('BuildPlan', {'operations': [{'op': 'setblock'}]}),
])
def test_rejects_v11_product_with_any_construction_signal(tmp_path, field, value):
    wrapper = _product(example_library())
    wrapper[field] = value
    with pytest.raises(StudioError, match='未施工'):
        read_external_b143_source(_file(tmp_path, wrapper))


def test_product_cannot_smuggle_new_engine_geometry_or_source_shapes(tmp_path):
    wrapper = _product(copy.deepcopy(example_library()))
    wrapper['design']['elements'][0]['repeat'] = {'count': 2, 'offset': [4,0,0]}
    with pytest.raises(StudioError, match='固定B143に未対応'):
        read_external_b143_source(_file(tmp_path, wrapper))
    # Bad/missing canonical designs still reject, even inside a safe wrapper.
    wrapper['design'] = {'id': 'not-an-engine-source'}
    with pytest.raises(StudioError, match='B143設計JSONではありません'):
        read_external_b143_source(_file(tmp_path, wrapper))


def test_gui_v11_product_must_remain_unapproved_until_explicit_save(studio, tmp_path, monkeypatch):
    app, _ = studio
    before = app.history.head()['revisionId']
    design = copy.deepcopy(example_library())
    design['title'] = 'V11 TRUE B143 PRODUCT 提案'
    source = _file(tmp_path, _product(design), name='TRUE_B143_PRODUCT.json')
    monkeypatch.setattr(studio_main.filedialog, 'askopenfilename', lambda **kw: str(source))
    monkeypatch.setattr(studio_main, 'run_bridge', lambda _engine, doc: _scene(doc))
    app.import_v11_btn.invoke()
    _complete(app)
    assert app.pending_revision['kind'] == 'new'
    assert app.history.head()['revisionId'] == before
    assert app.preview.scene['title'] == design['title']
    assert app.pending_revision['scene']['primitives'] == _scene(design)['primitives']
    app.reject_draft_btn.invoke()
    assert app.history.head()['revisionId'] == before


def test_accepts_only_canonical_source_data_without_mutating_file(tmp_path):
    source = _file(tmp_path, example_library())
    original = source.read_bytes()
    result = read_external_b143_source(source)
    assert result == example_library()
    assert source.read_bytes() == original


@pytest.mark.parametrize('invalid', [
    {'schema': 'r125-architecture-plan-v1', 'id': 'plan', 'designOnly': True},
    {'scene': example_library()},
    {**example_library(), 'designOnly': False},
])
def test_rejects_plan_wrapper_and_non_design_only(tmp_path, invalid):
    with pytest.raises(StudioError):
        read_external_b143_source(_file(tmp_path, invalid))


@pytest.mark.parametrize('field', ['repeat', 'staircase'])
def test_rejects_v11_new_grammar_without_reimplementing_engine(tmp_path, field):
    design = copy.deepcopy(example_library())
    if field == 'repeat':
        design['elements'][0]['repeat'] = {'count': 2, 'offset': [4, 0, 0]}
    else:
        design['elements'][0]['shape'] = 'staircase'
    with pytest.raises(StudioError, match='固定B143に未対応'):
        read_external_b143_source(_file(tmp_path, design))


def test_rejects_oversized_non_utf8_and_bad_extension(tmp_path):
    bad = tmp_path / 'large.json'
    bad.write_bytes(b'a' * (MAX_SOURCE_JSON_BYTES + 1))
    with pytest.raises(StudioError, match='1MiB'):
        read_external_b143_source(bad)
    bad.write_bytes(b'\xff')
    with pytest.raises(StudioError, match='UTF-8'):
        read_external_b143_source(bad)
    with pytest.raises(StudioError, match='.json'):
        read_external_b143_source(_file(tmp_path, example_library(), 'design.txt'))


def test_gui_external_source_stays_draft_until_explicit_approval(studio, tmp_path, monkeypatch):
    app, _ = studio
    original = app.history.head()['revisionId']
    prior_output = (app.manager.root / 'outputs' / 'latest-design.json').read_bytes()
    design = copy.deepcopy(example_library())
    design['title'] = 'V11から読み込んだ設計'
    source = _file(tmp_path, design)
    original_bytes = source.read_bytes()
    monkeypatch.setattr(studio_main.filedialog, 'askopenfilename', lambda **kw: str(source))
    monkeypatch.setattr(studio_main, 'run_bridge', lambda _engine, doc: _scene(doc))
    monkeypatch.setattr(studio_main, 'ask_llm', lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError('Import must not infer')))
    app.import_v11_btn.invoke()
    _complete(app)
    assert app.pending_revision['kind'] == 'new'
    assert app.preview.scene['title'] == design['title']
    assert app.history.head()['revisionId'] == original
    assert (app.manager.root / 'outputs' / 'latest-design.json').read_bytes() == prior_output
    assert source.read_bytes() == original_bytes
    assert 'disabled' in app.import_v11_btn.state()
    app.approve_draft_btn.invoke()
    _complete(app)
    assert app.history.head()['design']['title'] == design['title']
    assert app.history.head()['worldWrites'] == 0
    assert source.read_bytes() == original_bytes


def test_gui_invalid_external_source_does_not_replace_approved(studio, tmp_path, monkeypatch):
    app, _ = studio
    before = app.history.head()['revisionId']
    design = copy.deepcopy(example_library())
    design['elements'][0]['repeat'] = {'count': 2, 'offset': [4,0,0]}
    source = _file(tmp_path, design)
    errors=[]
    monkeypatch.setattr(studio_main.filedialog, 'askopenfilename', lambda **kw: str(source))
    monkeypatch.setattr(studio_main.messagebox, 'showerror', lambda *args: errors.append(args))
    app.import_v11_btn.invoke()
    _complete(app)
    assert errors
    assert app.pending_revision is None
    assert app.history.head()['revisionId'] == before


def test_gui_external_source_cancel_discard_and_conflict(studio, tmp_path, monkeypatch):
    app, _ = studio
    before = app.history.head()['revisionId']
    design = copy.deepcopy(example_library())
    design['title'] = '外部インポート候補'
    source = _file(tmp_path, design)
    monkeypatch.setattr(studio_main.filedialog, 'askopenfilename', lambda **kw: str(source))
    monkeypatch.setattr(studio_main, 'run_bridge', lambda _engine, doc: _scene(doc))
    app.import_v11_btn.invoke()
    _complete(app)
    app.reject_draft_btn.invoke()
    assert app.history.head()['revisionId'] == before
    app.import_v11_btn.invoke()
    _complete(app)
    external = copy.deepcopy(example_library())
    external['title'] = '別セッションで承認'
    app.history.commit(external, _scene(external), engine_commit=app.manager.active_sha)
    latest = app.history.head()['revisionId']
    monkeypatch.setattr(studio_main.messagebox, 'showerror', lambda *_: None)
    app.approve_draft_btn.invoke()
    _complete(app)
    assert app.pending_revision is not None
    assert app.history.head()['revisionId'] == latest
