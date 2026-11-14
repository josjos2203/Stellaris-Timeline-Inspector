import os
import sys
import json
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import urllib.parse
from setup_config import get_stellaris_dir

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STELLARIS_DIR = get_stellaris_dir(auto_prompt=True)
PORT = 8765

if not STELLARIS_DIR:
    print("[!] Cannot start server without a valid Stellaris installation path. Run setup.cmd first.")
    os.system("pause")
    sys.exit(1)

class StellarisHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == '/api/list':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            output_dir = os.path.join(SCRIPT_DIR, 'output')
            os.makedirs(output_dir, exist_ok=True)
            files = sorted([f for f in os.listdir(output_dir) if f.endswith('.json')])
            self.wfile.write(json.dumps(files).encode())
            return

        if parsed.path == '/api/meta':
            qs = urllib.parse.parse_qs(parsed.query)
            filename = qs.get('file', [''])[0]
            target_path = os.path.join(SCRIPT_DIR, 'output', os.path.basename(filename))
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            if filename and os.path.exists(target_path):
                stat = os.stat(target_path)
                self.wfile.write(json.dumps({"mtime": stat.st_mtime, "size": stat.st_size}).encode())
            else:
                self.wfile.write(json.dumps({"mtime": 0, "size": 0}).encode())
            return
            
        super().do_GET()

    def translate_path(self, path):
        clean_path = path.split('?', 1)[0].split('#', 1)[0]
        if clean_path.startswith("/gfx/") or clean_path.startswith("/flags/"):
            rel_path = urllib.parse.unquote(clean_path.lstrip("/")).replace("/", os.sep)
            return os.path.join(STELLARIS_DIR, rel_path)
        if clean_path.startswith("/output/"):
            rel_file = urllib.parse.unquote(clean_path.replace("/output/", "")).replace("/", os.sep)
            return os.path.join(SCRIPT_DIR, "output", rel_file)
            
        return super().translate_path(path)

def run_server():
    os.chdir(SCRIPT_DIR)
    server_address = ('127.0.0.1', PORT)
    httpd = ThreadingHTTPServer(server_address, StellarisHandler)
    
    print(f"Using Stellaris Assets from: {STELLARIS_DIR}")
    print(f"Stellaris Timeline Server (Multi-Threaded Live Mode) running on http://127.0.0.1:{PORT}")
    print("Close this window to shut down the server.")
    
    webbrowser.open(f'http://127.0.0.1:{PORT}/index.html')
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()
        sys.exit(0)

if __name__ == '__main__':
    run_server()