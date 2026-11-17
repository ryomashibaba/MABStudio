"""Local protocol tests: no actual AI model and no world connection."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import pytest
from app import core

DESIGN = {
 'schema': core.SCHEMA,'id':'local-llm-bridge','title':'Local AI design',
 'brief':'Offline library','designOnly':True,
 'bounds':{'width':12,'height':12,'depth':12},
 'elements':[{'id':'base','shape':'box','mode':'add','material':'stone',
              'origin':[2,1,2],'size':[8,3,8]}]
}

class Handler(BaseHTTPRequestHandler):
    requests = []
    def log_message(self, *args):
        return
    def do_GET(self):
        if self.path == '/api/tags':
            body={'models':[{'name':'test-model'}]}
        elif self.path == '/v1/models':
            body={'data':[{'id':'test-model'}]}
        else:
            self.send_error(404);return
        raw=json.dumps(body).encode()
        self.send_response(200);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.requests.append(data)
        if self.path == '/api/chat':
            body={'message':{'content':json.dumps(DESIGN)}}
        elif self.path == '/v1/chat/completions':
            body={'choices':[{'message':{'content':json.dumps(DESIGN)}}]}
        else:
            self.send_error(404);return
        raw=json.dumps(body).encode()
        self.send_response(200);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)

@pytest.mark.parametrize('provider', ['Ollama','LM Studio'])
def test_local_llm_json_protocol_and_compilation(tmp_path,monkeypatch,provider):
    server=HTTPServer(('127.0.0.1',0),Handler)
    server.RequestHandlerClass.requests=[]
    t=threading.Thread(target=server.serve_forever,daemon=True)
    t.start()
    try:
        base=f'http://127.0.0.1:{server.server_address[1]}'
        monkeypatch.setattr(core,'OLLAMA_BASE',base)
        monkeypatch.setattr(core,'LM_STUDIO_BASE',base)
        assert core.llm_models(provider)==['test-model']
        result=core.ask_llm(provider,'test-model','日本語で図書館を作って')
        assert result==DESIGN
        from tests.test_core import fake_engine
        scene=core.run_bridge(fake_engine(tmp_path/'test-engine'),result)
        assert scene['stats']['voxels']==192
        assert scene['BuildPlan']['operations']==[]
        assert server.RequestHandlerClass.requests[0]['messages'][1]['content']=='日本語で図書館を作って'
        # Corrective prompt includes prior JSON to target the error, no executable code.
        core.ask_llm(provider,'test-model','日本語で図書館を作って',
                     error='B143_OUTSIDE_LOCAL_BOUNDS',previous_design=DESIGN)
        assert '修正する前回の設計JSON' in server.RequestHandlerClass.requests[-1]['messages'][1]['content']
    finally:
        server.shutdown();server.server_close();t.join(timeout=2)
