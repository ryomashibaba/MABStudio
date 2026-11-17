"""No live model/world needed: local redirect refusal and preview controls."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import pytest
from app import core
from app.preview import VoxelPreview


class RedirectHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(302)
        self.send_header('Location', 'https://example.invalid/model-leak')
        self.end_headers()

    def do_POST(self):
        self.send_response(307)
        self.send_header('Location', 'https://example.invalid/model-leak')
        self.end_headers()


@pytest.mark.parametrize('provider', ['Ollama', 'LM Studio'])
def test_redirect_to_external_is_never_followed(provider, monkeypatch):
    server = HTTPServer(('127.0.0.1', 0), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        local_url = f'http://127.0.0.1:{server.server_port}'
        monkeypatch.setattr(core, 'OLLAMA_BASE', local_url)
        monkeypatch.setattr(core, 'LM_STUDIO_BASE', local_url)
        with pytest.raises(core.StudioError, match='リダイレクト'):
            core.llm_models(provider)
        with pytest.raises(core.StudioError, match='リダイレクト'):
            core.ask_llm(provider, 'test-model', 'Minecraftの家を作って')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_preview_detail_limits_are_bounded():
    assert VoxelPreview.DETAIL_LIMITS['標準（2,300）'] == 2300
    assert VoxelPreview.DETAIL_LIMITS['精細（8,000）'] == 8000
    assert VoxelPreview.DETAIL_LIMITS['高精細（15,000）'] == 15000
    assert max(v for v in VoxelPreview.DETAIL_LIMITS.values() if v is not None) <= 15000
