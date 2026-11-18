"""Local fake HTTP API only: structured output compatibility, no LLM/World."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from contextlib import contextmanager
import pytest
from app import core
from tests.test_local_llm_flow import DESIGN


@contextmanager
def model_server(mode):
    seen=[]
    class Model(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.path == '/api/ps':
                body = {'models': [{'name': 'test-model'}]}
            elif self.path == '/api/v1/models':
                body = {'models': [{'key': 'test-model', 'type': 'llm',
                          'loaded_instances': [{'id': 'test-model'}]}]}
            else:
                self.send_error(404)
                return
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append(body)
            if mode=='reject-structured' and (isinstance(body.get('format'),dict) or
                    body.get('response_format',{}).get('type')=='json_schema'):
                raw=b'{"error":"unsupported response_format / json_schema"}'
                self.send_response(400)
                self.send_header('Content-Length',str(len(raw)));self.end_headers()
                self.wfile.write(raw);return
            if mode in ('server-error','generic-bad-request'):
                raw=b'{"error":"model not loaded"}'
                self.send_response(500 if mode=='server-error' else 400)
                self.send_header('Content-Length',str(len(raw)));self.end_headers()
                self.wfile.write(raw);return
            is_ollama=self.path=='/api/chat'
            if is_ollama:
                reply={'message':{'content':json.dumps(DESIGN)},'done':True,'done_reason':'length' if mode=='truncated' else 'stop'}
            else:
                reply={'choices':[{'message':{'content':json.dumps(DESIGN)},
                                  'finish_reason':'length' if mode=='truncated' else 'stop'}]}
            raw=json.dumps(reply).encode()
            self.send_response(200)
            self.send_header('Content-Length',str(len(raw)));self.end_headers()
            self.wfile.write(raw)
    srv=HTTPServer(('127.0.0.1',0),Model)
    thread=threading.Thread(target=srv.serve_forever,daemon=True)
    thread.start()
    try:yield f'http://127.0.0.1:{srv.server_port}',seen
    finally:
        srv.shutdown();srv.server_close();thread.join(timeout=2)

@pytest.mark.parametrize('provider',['Ollama','LM Studio'])
def test_structured_request_on_local_protocol(provider,monkeypatch):
    with model_server('valid') as (url,requests):
        monkeypatch.setattr(core,'OLLAMA_BASE',url)
        monkeypatch.setattr(core,'LM_STUDIO_BASE',url)
        assert core.ask_llm(provider,'test-model','図書館を作って')==DESIGN
        assert len(requests)==1
        schema=requests[0]['format'] if provider=='Ollama' else requests[0]['response_format']['json_schema']['schema']
        assert schema['properties']['schema']['const']==core.SCHEMA
        assert schema['properties']['elements']['maxItems']==core.MAX_MODEL_ELEMENTS
        assert requests[0]['messages'][1]['content']=='図書館を作って'
        assert requests[0]['stream'] is (provider=='LM Studio')

@pytest.mark.parametrize('provider',['Ollama','LM Studio'])
def test_explicit_unsupported_schema_fails_without_second_post(provider,monkeypatch):
    with model_server('reject-structured') as (url,requests):
        monkeypatch.setattr(core,'OLLAMA_BASE',url)
        monkeypatch.setattr(core,'LM_STUDIO_BASE',url)
        with pytest.raises(core.ModelTransportError, match='自動再試行'):
            core.ask_llm(provider,'test-model','図書館を作って')
        assert len(requests)==1

@pytest.mark.parametrize('provider',['Ollama','LM Studio'])
def test_http500_no_repeat_inference(provider,monkeypatch):
    with model_server('server-error') as (url,requests):
        monkeypatch.setattr(core,'OLLAMA_BASE',url)
        monkeypatch.setattr(core,'LM_STUDIO_BASE',url)
        with pytest.raises(core.ModelTransportError,match='HTTP 500'):
            core.ask_llm(provider,'test-model','図書館を作って')
        assert len(requests)==1

@pytest.mark.parametrize('provider',['Ollama','LM Studio'])
def test_truncated_answer_rejected(provider,monkeypatch):
    with model_server('truncated') as (url,requests):
        monkeypatch.setattr(core,'OLLAMA_BASE',url)
        monkeypatch.setattr(core,'LM_STUDIO_BASE',url)
        with pytest.raises(core.StudioError,match='トークン上限'):
            core.ask_llm(provider,'test-model','図書館を作って')
        assert len(requests)==1

@pytest.mark.parametrize('provider',['Ollama','LM Studio'])
def test_generic_bad_request_is_not_mistaken_for_schema_unsupported(provider,monkeypatch):
    with model_server('generic-bad-request') as (url,requests):
        monkeypatch.setattr(core,'OLLAMA_BASE',url)
        monkeypatch.setattr(core,'LM_STUDIO_BASE',url)
        with pytest.raises(core.ModelTransportError,match='HTTP 400'):
            core.ask_llm(provider,'unloaded-model','図書館を作って')
        assert len(requests)==1  # avoid unnecessary expensive retry


@pytest.mark.parametrize('name', [
    'qwen3:14b', 'qwen3:8b', 'QWEN3:latest', 'qwen3.8-27b',
    'qwen.qwen3.8-27b', 'library/qwen3:14b',
])
def test_qwen3_ollama_requests_non_thinking_without_loosening_schema(name, monkeypatch):
    with model_server('valid') as (url, requests):
        monkeypatch.setattr(core, 'OLLAMA_BASE', url)
        assert core.ask_llm('Ollama', name, '衛星の整備施設を作って') == DESIGN
        assert len(requests) == 1
        req = requests[0]
        assert req['think'] is False
        assert req['stream'] is False
        assert req['format']['properties']['schema']['const'] == core.SCHEMA
        assert req['format']['properties']['elements']['maxItems'] == core.MAX_MODEL_ELEMENTS
        assert 'keep_alive' not in req


@pytest.mark.parametrize('name', ['test-model', 'gemma4:26b', 'qwen2.5:14b', 'myqwen3:14b', 'qwen3ish'])
def test_other_ollama_models_do_not_inherit_qwen_thinking_setting(name, monkeypatch):
    with model_server('valid') as (url, requests):
        monkeypatch.setattr(core, 'OLLAMA_BASE', url)
        assert core.ask_llm('Ollama', name, '図書館を作って') == DESIGN
        assert len(requests) == 1
        assert 'think' not in requests[0]


def test_qwen3_delta_revision_keeps_thinking_off_and_delta_schema(monkeypatch):
    from tests.test_local_llm_flow import DESIGN
    from unittest.mock import patch
    delta = {'schema': core.REVISION_DELTA_SCHEMA_ID,
             'replace': [{'id': 'base', 'shape': 'box', 'mode': 'add',
                          'material': 'glass', 'origin': [2, 1, 2], 'size': [8, 3, 8]}],
             'add': [], 'removeIds': []}
    payloads = []
    def fake_response(url, payload, timeout, on_progress=None, cancel_event=None):
        payloads.append(payload)
        return {'message': {'content': json.dumps(delta)}, 'done': True, 'done_reason': 'stop'}
    with patch.object(core, '_model_response', side_effect=fake_response):
        revised = core.ask_llm('Ollama', 'qwen3:14b', '床をガラスに変える',
                               revision_base=DESIGN, revision_mode='delta')
    assert len(payloads) == 1
    assert payloads[0]['think'] is False
    assert payloads[0]['format']['properties']['schema']['const'] == core.REVISION_DELTA_SCHEMA_ID
    assert revised['elements'][0]['material'] == 'glass'
    assert DESIGN['elements'][0]['material'] == 'stone'


def test_ollama_thought_only_does_not_become_a_design(monkeypatch):
    from unittest.mock import patch
    reply = {'message': {'content': '', 'thinking': 'nonempty thought'},
             'done': True, 'done_reason': 'stop'}
    with patch.object(core, '_model_response', return_value=reply) as request:
        with pytest.raises(core.ModelOutputLimitError, match='思考用出力だけ'):
            core.ask_llm('Ollama', 'qwen3:14b', '図書館を作って')
    assert request.call_count == 1
