"""SSE test doubles: no genuine local-model inference or Minecraft connections."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
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
            if mode == 'stall':
                time.sleep(.7)
            if mode == 'stall_cancel':
                time.sleep(1.4)
            mid = len(content)//2
            send({'choices':[{'delta':{'content':content[:mid]},'finish_reason':None}]})
            if mode == 'slow':
                time.sleep(.3)
            send({'choices':[{'delta':{'content':content[mid:]},'finish_reason':None}]})
            send({'choices':[{'delta':{},'finish_reason':('length' if mode=='length' else 'content_filter' if mode=='filtered' else 'stop')}]})
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
        assert seen[0]['response_format']['json_schema']['schema']['properties']['elements']['maxItems']==24


def test_cancel_closes_local_stream_and_never_retries(monkeypatch):
    token=threading.Event()
    result=[]
    entered=threading.Event();opened=[]
    class FakeSocket:
        def settimeout(self,_):
            raise AssertionError('Never apply short timeout to buffered HTTPResponse')
        def shutdown(self,_): pass
    class Response:
        headers={'Content-Type':'text/event-stream'}
        fp=type('FP',(),{'raw':type('Raw',(),{'_sock':FakeSocket()})()})()
        closed=False
        def __enter__(self): return self
        def __exit__(self,*_): self.closed=True
        def readline(self,_limit):
            entered.set()
            token.wait(2)
            raise socket.timeout('test double waits for cancellation')
    response=Response()
    def open_once(req,timeout):
        opened.append(req.full_url)
        return response
    monkeypatch.setattr(core,'LOCAL_HTTP',type('FakeTransport',(),{
        'open':staticmethod(open_once)})())
    worker=threading.Thread(target=lambda:_capture_stream_cancel(result,token),daemon=True)
    worker.start()
    assert entered.wait(1),repr(result)
    token.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert len(opened)==1
    assert response.closed
    assert len(result)==1 and isinstance(result[0],core.ModelCancelled)



def test_sse_idle_gap_older_than_quarter_second_does_not_poison_http_stream(monkeypatch):
    """Reproduces the Windows popup: 0.25s socket timeout poisons readline().

    No genuine LM Studio inference, and exactly one HTTP POST.
    """
    with fake_lm_stream('stall') as (url, seen):
        monkeypatch.setattr(core, 'LM_STUDIO_BASE', url)
        token = threading.Event()
        assert core.ask_llm('LM Studio', 'gemma-test', '塔を建てる',
                            cancel_event=token) == DESIGN
        assert len(seen) == 1


def test_real_socket_cancel_interrupts_sse_without_retry(monkeypatch):
    """Cancellation interrupts a blocking read instead of triggering timeout reuse."""
    with fake_lm_stream('stall_cancel') as (url, seen):
        monkeypatch.setattr(core, 'LM_STUDIO_BASE', url)
        token = threading.Event()
        outcome = []
        worker = threading.Thread(target=lambda: _capture_cancel(outcome, token), daemon=True)
        worker.start()
        # Wait until the streaming request was received; its next SSE chunk is
        # intentionally delayed for 1.4 seconds by the local fake server.
        deadline = time.monotonic() + 2
        while not seen and time.monotonic() < deadline:
            time.sleep(.01)
        assert len(seen) == 1
        time.sleep(.08)
        started = time.monotonic()
        token.set()
        worker.join(timeout=.9)
        assert not worker.is_alive(), 'A cancelled socket read must be interrupted promptly'
        assert time.monotonic() - started < .9
        assert len(outcome) == 1 and isinstance(outcome[0], core.ModelCancelled)
        assert len(seen) == 1  # never automatically resend


def test_pre_cancelled_request_never_posts(monkeypatch):
    token=threading.Event();token.set()
    posts=[]
    monkeypatch.setattr(core,'LOCAL_HTTP',type('NoNetwork',(),{
        'open':staticmethod(lambda *a,**k: posts.append((a,k)))})())
    with pytest.raises(core.ModelCancelled):
        core.ask_llm('LM Studio','gemma-test','建築を作る',cancel_event=token)
    assert posts==[]


def _capture_cancel(result, token):
    try:
        core.ask_llm('LM Studio','gemma-test','建築を作る',cancel_event=token)
    except Exception as exc:
        result.append(exc)


def _capture_stream_cancel(result,token):
    try:
        core._model_response('http://127.0.0.1:1234/test',{'stream':True},180,
                            cancel_event=token)
    except Exception as exc:
        result.append(exc)


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
    assert core.MODEL_GENERATION_SCHEMA['properties']['elements']['maxItems']==24
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


def test_streamed_valid_json_is_not_accepted_after_abnormal_finish(monkeypatch):
    with fake_lm_stream('filtered') as (url, seen):
        monkeypatch.setattr(core, 'LM_STUDIO_BASE', url)
        with pytest.raises(core.ModelTransportError, match='正常終了していません'):
            core.ask_llm('LM Studio', 'gemma-test', '図書館を作って')
        assert len(seen) == 1
