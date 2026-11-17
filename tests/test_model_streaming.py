"""SSE test doubles: no genuine local-model inference or Minecraft connections."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import pytest
from app import core
from tests.test_local_llm_flow import DESIGN


@contextmanager
def fake_lm_stream(mode='normal'):
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            return
        def do_GET(self):
            self.send_error(404)
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append(request)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            def send(x):
                data = ('data: '+json.dumps(x, ensure_ascii=False)+'\n\n').encode()
                self.wfile.write(data)
                self.wfile.flush()
            content = json.dumps(DESIGN, ensure_ascii=False)
            send({'choices':[{'delta':{'role':'assistant','reasoning_content':'private'},'finish_reason':None}]})
            if mode == 'broken':
                return
            mid = len(content)//2
            send({'choices':[{'delta':{'content':content[:mid]},'finish_reason':None}]})
            if mode == 'slow':
                time.sleep(.3)
            send({'choices':[{'delta':{'content':content[mid:]},'finish_reason':None}]})
            send({'choices':[{'delta':{},'finish_reason':'length' if mode=='length' else 'stop'}]})
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
    srv = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=srv.serve_forever,daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{srv.server_port}', seen
    finally:
        srv.shutdown();srv.server_close();thread.join(timeout=2)


def test_sse_assembles_chunks_without_reasoning_content(monkeypatch):
    notices=[]
    with fake_lm_stream('slow') as (url, seen):
        monkeypatch.setattr(core,'LM_STUDIO_BASE',url)
        monkeypatch.setattr(core,'MODEL_STREAM_IDLE_TIMEOUT',2)
        assert core.ask_llm('LM Studio','gemma-4-test','未来的な4階建てのビル',
                            on_progress=notices.append,timeout=1)==DESIGN
        assert len(seen)==1
        assert seen[0]['stream'] is True
        assert seen[0]['response_format']['json_schema']['schema']['properties']['elements']['maxItems']==18


def test_sse_length_never_uses_partial_json(monkeypatch):
    with fake_lm_stream('length') as (url, seen):
        monkeypatch.setattr(core,'LM_STUDIO_BASE',url)
        with pytest.raises(core.ModelOutputLimitError,match='トークン上限') as info:
            core.ask_llm('LM Studio','gemma-4-test','未来的な4階建てのビル')
        assert 'JSON本文' in str(info.value)
        assert '思考用出力7文字' in str(info.value)
        assert len(seen)==1


def test_sse_broken_stream_fails_closed_and_no_auto_retry(monkeypatch):
    with fake_lm_stream('broken') as (url, seen):
        monkeypatch.setattr(core,'LM_STUDIO_BASE',url)
        with pytest.raises(core.ModelTransportError,match='完了前に切断'):
            core.ask_llm('LM Studio','gemma-4-test','未来的な4階建てのビル')
        assert len(seen)==1


def test_qwen38_requests_nonthinking_without_changing_model_load(monkeypatch):
    with fake_lm_stream() as (url, seen):
        monkeypatch.setattr(core,'LM_STUDIO_BASE',url)
        assert core.ask_llm('LM Studio','qwen.qwen3.8-27b','未来的な4階建てのビル')==DESIGN
        assert len(seen)==1
        assert seen[0]['chat_template_kwargs']=={'enable_thinking':False}
        assert seen[0]['stream'] is True
        assert len(seen[0]['messages'])==2


def test_model_generation_schema_does_not_change_mab_contract():
    assert core.B143_JSON_SCHEMA['properties']['elements']['maxItems']==160
    assert core.MODEL_GENERATION_SCHEMA['properties']['elements']['maxItems']==18
    assert core.MODEL_GENERATION_SCHEMA['properties']['title']['maxLength']==48
    assert core.MODEL_GENERATION_SCHEMA['properties']['brief']['maxLength']==120
    assert core.B143_JSON_SCHEMA['properties']['brief']['maxLength']==500


def test_revision_does_not_shrink_existing_40_element_design(monkeypatch):
    large = {**DESIGN, 'elements': [{**DESIGN['elements'][0], 'id': 'base'+str(i)} for i in range(40)]}
    with fake_lm_stream() as (url,seen):
        monkeypatch.setattr(core,'LM_STUDIO_BASE',url)
        # The fixture response is small; this test checks the GENERATION BOUNDARY.
        assert core.ask_llm('LM Studio','gemma-4-test','入口を改善',revision_base=large)==DESIGN
        constraint = seen[0]['response_format']['json_schema']['schema']
        assert constraint['properties']['elements']['maxItems']==48
        assert '最大48' in seen[0]['messages'][0]['content']
        assert constraint['properties']['title']['maxLength']==100
        assert constraint['properties']['brief']['maxLength']==500
        assert all(v['properties']['id'].get('maxLength') is None for v in constraint['properties']['elements']['items']['anyOf'])
        assert core.B143_JSON_SCHEMA['properties']['elements']['maxItems']==160
