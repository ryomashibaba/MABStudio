"""GitHub Actions ONLY: canned localhost model response, not genuine LLM inference."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
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
        self.write({'message':{'content':json.dumps(example_library(),ensure_ascii=False)},'done_reason':'stop'})
    def write(self,data):
        raw=json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

if __name__=='__main__':
    HTTPServer(('127.0.0.1',11434),Handler).serve_forever()
