"""Real loopback HTTP boundary tests; no local model or Minecraft connection."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

import pytest

from app import core
from tests.test_local_llm_flow import DESIGN


@contextmanager
def controlled_http(mode):
    requests = []
    received = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            requests.append(json.loads(body))
            received.set()
            if mode == 'header-stall':
                time.sleep(1.2)  # timeout watcher must interrupt this wait
            if mode == 'truncated-body':
                raw = json.dumps({'message': {'content': json.dumps(DESIGN)},
                                  'done': True, 'done_reason': 'stop'}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(raw) + 300))
                self.end_headers()
                self.wfile.write(raw)
                self.wfile.flush()
                return
            try:
                if mode in ('header-stall', 'body-stall'):
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.end_headers()
                    if mode == 'body-stall':
                        time.sleep(1.2)
                    self.wfile.write(b'data: [DONE]\n\n')
                else:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    if mode == 'ollama-incomplete':
                        reply = {'message': {'content': json.dumps(DESIGN)},
                                 'done': False, 'done_reason': 'stop'}
                    else:
                        reply = {'choices': [{'message': {'content': json.dumps(DESIGN)},
                                              'finish_reason': 'content_filter'}]}
                    self.wfile.write(json.dumps(reply).encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', requests, received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_cancel_while_waiting_for_headers_is_prompt_and_never_retries():
    with controlled_http('header-stall') as (url, requests, received):
        cancel = threading.Event()
        outcome = []

        def request():
            try:
                core._model_response(url + '/v1/chat/completions', {'stream': True},
                                     180, cancel_event=cancel)
            except Exception as exc:
                outcome.append(exc)

        worker = threading.Thread(target=request, daemon=True)
        worker.start()
        assert received.wait(1)
        start = time.monotonic()
        cancel.set()
        worker.join(0.6)
        assert not worker.is_alive(), 'Cancellation must interrupt before headers arrive'
        assert time.monotonic() - start < 0.6
        assert len(requests) == 1
        assert len(outcome) == 1 and isinstance(outcome[0], core.ModelCancelled)


@pytest.mark.parametrize('mode', ['header-stall', 'body-stall'])
def test_total_deadline_is_enforced_during_blocked_io(monkeypatch, mode):
    monkeypatch.setattr(core, 'MODEL_STREAM_TOTAL_TIMEOUT', 0.20)
    with controlled_http(mode) as (url, requests, received):
        start = time.monotonic()
        with pytest.raises(core.ModelTransportError, match='総時間上限'):
            core._model_response(url + '/v1/chat/completions', {'stream': True}, 180)
        assert time.monotonic() - start < 0.65
        assert len(requests) == 1


def test_ollama_partial_response_never_becomes_source(monkeypatch):
    with controlled_http('ollama-incomplete') as (url, requests, _):
        monkeypatch.setattr(core, 'OLLAMA_BASE', url)
        with pytest.raises(core.ModelTransportError, match='生成完了を確認できない'):
            core.ask_llm('Ollama', 'test-model', '図書館を作って')
        assert len(requests) == 1


def test_lm_studio_abnormal_finish_never_becomes_source(monkeypatch):
    with controlled_http('lm-abnormal') as (url, requests, _):
        monkeypatch.setattr(core, 'LM_STUDIO_BASE', url)
        with pytest.raises(core.ModelTransportError, match='正常終了していません'):
            core.ask_llm('LM Studio', 'test-model', '図書館を作って')
        assert len(requests) == 1


def test_nonstream_content_length_truncation_fails_closed():
    # Even a valid JSON prefix with done=true cannot be trusted when the HTTP
    # framing itself reports missing bytes. No second POST.
    with controlled_http('truncated-body') as (url, requests, _):
        with pytest.raises(core.ModelTransportError, match='途中で切断'):
            core._model_response(url + '/api/chat', {'stream': False}, 3)
        assert len(requests) == 1
