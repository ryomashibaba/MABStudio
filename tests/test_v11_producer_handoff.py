"""Producer-side handoff tests. No real model, engine mutation or world writes."""
from __future__ import annotations

import json

import pytest

from app.core import StudioError
from app.examples import example_library
from app import v11_publisher
from app.v11_watch import READY_NAME, newest_v11_ready_source


def test_publisher_exposes_only_real_b143_accepted_unapproved_design(tmp_path, monkeypatch):
    source = tmp_path / 'B143_DESIGN.json'
    original = example_library()
    source.write_text(json.dumps(original, ensure_ascii=False), encoding='utf-8')
    inbox = tmp_path / 'inbox'
    inbox.mkdir()
    calls = []

    def compile_with_pinned_engine(engine, design):
        calls.append((engine, design))
        return {'sourceSha256': 'a' * 64, 'designOnly': True,
                'readiness': 'NOT_PREPARED', 'worldContacts': 0,
                'BuildPlan': {'operations': []}, 'primitives': [[0, 0, 0]]}

    monkeypatch.setattr(v11_publisher, 'run_bridge', compile_with_pinned_engine)
    status = v11_publisher.publish_compatible_v11_source(source, tmp_path / 'engine', inbox)
    assert status['published'] is True
    assert calls[0][1] == original
    assert json.loads((inbox / READY_NAME).read_text()) == original
    assert source.read_text() == json.dumps(original, ensure_ascii=False)
    assert v11_publisher.publish_compatible_v11_source(source, tmp_path / 'engine', inbox)['published'] is False
    # Published data are detectable by the *actual* Studio inbox watcher.
    (tmp_path / 'v11-inbox').mkdir()
    (tmp_path / 'v11-inbox' / READY_NAME).write_bytes((inbox / READY_NAME).read_bytes())
    assert newest_v11_ready_source(tmp_path)[2] == original['title']
    assert not (tmp_path / 'projects').exists()


def test_incompatible_v11_candidate_does_not_replace_prior_ready_source(tmp_path, monkeypatch):
    inbox = tmp_path / 'inbox'
    inbox.mkdir()
    ready = inbox / READY_NAME
    ready.write_bytes(b'previous ready source')
    invalid = example_library()
    invalid['elements'][0]['repeat'] = {'count': 2, 'offset': [2, 0, 0]}
    path = tmp_path / 'B143_DESIGN.json'
    path.write_text(json.dumps(invalid), encoding='utf-8')
    monkeypatch.setattr(v11_publisher, 'run_bridge', lambda *_: (_ for _ in ()).throw(AssertionError('must not compile')))
    with pytest.raises(StudioError, match='固定B143に未対応'):
        v11_publisher.publish_compatible_v11_source(path, tmp_path / 'engine', inbox)
    assert ready.read_bytes() == b'previous ready source'


def test_engine_rejection_leaves_existing_publication_unchanged(tmp_path, monkeypatch):
    inbox = tmp_path / 'inbox'
    inbox.mkdir()
    ready = inbox / READY_NAME
    ready.write_bytes(b'previous ready source')
    path = tmp_path / 'B143_DESIGN.json'
    path.write_text(json.dumps(example_library()), encoding='utf-8')

    def fail(*_):
        raise StudioError('B143_OUTSIDE_LOCAL_BOUNDS')

    monkeypatch.setattr(v11_publisher, 'run_bridge', fail)
    with pytest.raises(StudioError, match='B143_OUTSIDE_LOCAL_BOUNDS'):
        v11_publisher.publish_compatible_v11_source(path, tmp_path / 'engine', inbox)
    assert ready.read_bytes() == b'previous ready source'
