"""One-shot reasoning-only Gemma 4 regression; no genuine model or world I/O."""
import io
import json

import pytest

from app import core
from tests.test_local_llm_flow import DESIGN


def _sse_stream(events):
    return b''.join(('data: ' + json.dumps(e) + '\n\n').encode() for e in events) + b'data: [DONE]\n\n'


class _LocalResponse(io.BytesIO):
    headers = {'Content-Type': 'text/event-stream'}
    def __enter__(self):
        return self
    def __exit__(self, *_):
        self.close()


def _fake_open(monkeypatch, body):
    posts = []
    def open_request(req, timeout=None):
        posts.append(json.loads(req.data))
        return _LocalResponse(body)
    monkeypatch.setattr(core, 'LOCAL_HTTP', type('LocalOnly', (), {'open': staticmethod(open_request)})())
    monkeypatch.setattr(core, '_output_budget', lambda *args: (6392,8192))
    return posts


def test_gemma4_thought_only_stops_early_no_json_no_retry(monkeypatch):
    events = [{'choices': [{'delta': {'reasoning_content': '思' * 1000},
                            'finish_reason': None}]} for _ in range(18)]
    events.append({'choices': [{'delta': {}, 'finish_reason': 'length'}]})
    posts = _fake_open(monkeypatch, _sse_stream(events))
    with pytest.raises(core.ModelOutputLimitError, match='早期中止') as exc:
        core.ask_llm('LM Studio', 'gemma-4-26b-a4b-it-qat', '窓を大きくする',
                     revision_base=DESIGN, revision_mode='delta')
    assert '8000文字' in str(exc.value)
    assert len(posts) == 1  # no automatic repeat, no server/model configuration mutation
    assert posts[0]['chat_template_kwargs'] == {'enable_thinking': False}
    assert posts[0]['max_tokens'] == 6392
    assert posts[0]['stream'] is True


def test_gemma4_returns_json_after_some_reasoning(monkeypatch):
    content = json.dumps(DESIGN, ensure_ascii=False)
    events = [
        {'choices': [{'delta': {'reasoning_content':'a' * 2400}, 'finish_reason':None}]},
        {'choices': [{'delta': {'content':content}, 'finish_reason':None}]},
        {'choices': [{'delta': {}, 'finish_reason':'stop'}]},
    ]
    posts = _fake_open(monkeypatch, _sse_stream(events))
    assert core.ask_llm('LM Studio', 'gemma-4-26b-a4b-it-qat', '塔を建てる') == DESIGN
    assert len(posts) == 1


def test_nongemma_thinking_is_not_subject_to_gemma_guard(monkeypatch):
    content = json.dumps(DESIGN, ensure_ascii=False)
    events = [
        {'choices': [{'delta': {'reasoning_content':'a' * 10000}, 'finish_reason':None}]},
        {'choices': [{'delta': {'content':content}, 'finish_reason':None}]},
        {'choices': [{'delta': {}, 'finish_reason':'stop'}]},
    ]
    posts = _fake_open(monkeypatch, _sse_stream(events))
    assert core.ask_llm('LM Studio', 'unrelated-model', '塔を建てる') == DESIGN
    assert 'chat_template_kwargs' not in posts[0]
