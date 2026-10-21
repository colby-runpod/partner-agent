"""Health-only process: no Slack receiver, polling, imports of workers, or business writes."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        healthy = self.path == '/healthz'
        payload = json.dumps({'status': 'standby', 'processing_enabled': False}).encode()
        self.send_response(200 if healthy else 503)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        # Never acknowledge a Slack event that has not been durably accepted.
        self.send_response(503)
        self.end_headers()


def serve():
    HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
