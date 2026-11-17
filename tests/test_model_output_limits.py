"""Regression for actual Windows report: LM Studio stopped at 4500 output tokens.

All network exchanges are a loopback test double; this does NOT certify real
Gemma generation or semantic architectural quality.
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading
import pytest

from app import core
from tests.test_local_llm_flow import DESIGN


@contextmanager
def local_model_response(context=8192, *, finish='stop', usage=0, malformed_metadata=False):
    posts=[]
    gets=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            gets.append(self.path)
            if self.path != '/api/v1/models':
                self.send_error(404)
                return
            info = {'models': [{'key':'other-model','loaded_instances':[{'id':'other','config':{'context_length':65536}}]},
                               {'key':'gemma-test','loaded_instances':[{'id':'gemma-test','config':{'context_length':context}}]}]}
            data = b'{}' if malformed_metadata else json.dumps(info).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Length',str(len(data)));self.end_headers()
            self.wfile.write(data)
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            posts.append(body)
            reply={'choices':[{'message':{'content':json.dumps(DESIGN)},'finish_reason':finish}],
                   'usage':{'completion_tokens':usage}}
            raw=json.dumps(reply).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Length',str(len(raw)));self.end_headers()
            self.wfile.write(raw)
    server=HTTPServer(('127.0.0.1',0),Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    try:yield f'http://127.0.0.1:{server.server_port}', posts, gets
    finally:
        server.shutdown();server.server_close();worker.join(timeout=2)


def test_loaded_context_8192_expands_old_4500_ceiling(monkeypatch):
    with local_model_response(8192) as (url,posts,gets):
        monkeypatch.setattr(core,'LM_STUDIO_BASE',url)
        assert core.ask_llm('LM Studio','gemma-test','未来的な4階建てのビルを作って')==DESIGN
        assert gets==['/api/v1/models']
        assert len(posts)==1
        assert 5500<=posts[0]['max_tokens']<=6600
        assert posts[0]['response_format']['type']=='json_schema'
        assert posts[0]['stream'] is True


def test_loaded_context_4096_bounds_request(monkeypatch):
    with local_model_response(4096) as (url,posts,_):
        monkeypatch.setattr(core,'LM_STUDIO_BASE',url)
        core.ask_llm('LM Studio','gemma-test','図書館を作って')
        assert len(posts)==1
        assert 1024<=posts[0]['max_tokens']<4096


def test_missing_context_endpoint_falls_back_without_extra_inference(monkeypatch):
    with local_model_response(malformed_metadata=True) as (url,posts,_):
        monkeypatch.setattr(core,'LM_STUDIO_BASE',url)
        assert core.ask_llm('LM Studio','gemma-test','図書館を作って')==DESIGN
        assert posts[0]['max_tokens']==core.DEFAULT_OUTPUT_TOKENS
        assert len(posts)==1


def test_truncation_error_is_not_b143_rejection(monkeypatch):
    with local_model_response(8192,finish='length',usage=6300) as (url,posts,_):
        monkeypatch.setattr(core,'LM_STUDIO_BASE',url)
        with pytest.raises(core.ModelOutputLimitError) as error:
            core.ask_llm('LM Studio','gemma-test','未来的な4階建て')
        assert 'トークン上限' in str(error.value)
        assert 'SOURCEはまだ検証していません' in str(error.value)
        assert '生成済み6300' in str(error.value)
        assert len(posts)==1


def test_ollama_has_no_context_api_network_and_larger_token_budget(monkeypatch):
    from tests.test_local_ai_structured import model_server
    with model_server('valid') as (url,posts):
        monkeypatch.setattr(core,'OLLAMA_BASE',url)
        assert core.ask_llm('Ollama','test-model','図書館を作って')==DESIGN
        assert len(posts)==1
        assert posts[0]['options']['num_predict']==core.DEFAULT_OUTPUT_TOKENS
