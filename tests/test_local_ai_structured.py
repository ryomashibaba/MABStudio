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
                reply={'message':{'content':json.dumps(DESIGN)},'done_reason':'length' if mode=='truncated' else 'stop'}
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
def test_explicit_unsupported_schema_falls_back_once(provider,monkeypatch):
    with model_server('reject-structured') as (url,requests):
        monkeypatch.setattr(core,'OLLAMA_BASE',url)
        monkeypatch.setattr(core,'LM_STUDIO_BASE',url)
        assert core.ask_llm(provider,'test-model','図書館を作って')==DESIGN
        assert len(requests)==2
        assert (requests[1]['format']=='json' if provider=='Ollama' else
                requests[1]['response_format']['type']=='json_object')
        assert requests[0]['messages']==requests[1]['messages']

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
