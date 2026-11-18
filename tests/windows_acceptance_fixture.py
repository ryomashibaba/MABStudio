"""GitHub Actions ONLY: canned localhost model response, not genuine LLM inference."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import argparse
import json
from pathlib import Path
from app.examples import example_library

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        return
    def do_GET(self):
        if self.path!='/api/tags':
            self.send_error(404)
            return
        self.write({'models':[{'name':'CI_TEST_DOUBLE'}]})
    def do_POST(self):
        if self.path!='/api/chat':
            self.send_error(404)
            return
        length=int(self.headers.get('Content-Length','0'))
        if length<=0 or length>200000:
            self.send_error(400)
            return
        request=json.loads(self.rfile.read(length))
        if request.get('model')!='CI_TEST_DOUBLE' or request.get('stream') is not False:
            self.send_error(400)
            return
        self.write({'message':{'content':json.dumps(example_library(),ensure_ascii=False)},'done':True,'done_reason':'stop'})
    def write(self,data):
        raw=json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=11434)
    parser.add_argument('--ready-file',default=None)
    ns=parser.parse_args()
    if not 0<=ns.port<=65535:
        parser.error('port out of range')
    with HTTPServer(('127.0.0.1',ns.port),Handler) as server:
        if ns.ready_file:
            # Only records the ephemeral loopback port, never a credential or prompt.
            file=Path(ns.ready_file)
            file.parent.mkdir(parents=True,exist_ok=True)
            file.write_text(json.dumps({'port':server.server_port,'fixture':'CI_TEST_DOUBLE'}),encoding='utf-8')
        server.serve_forever()
