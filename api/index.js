from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse
import json

OWNER     = "@FizzaGirl"
DEVELOPER = "@FizzaGirl"
CHANNEL   = "@BUILDAPIS"

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = {
            "status": "ok",
            "name": "Parivahan Mobile Lookup API",
            "endpoints": {
                "GET /api/vehicle?rc=MH12DE1433": "Single vehicle mobile lookup",
                "GET /api/vehicle/MH12DE1433":    "Same, path-based",
            },
            "Owner":     OWNER,
            "Developer": DEVELOPER,
            "Channel":   CHANNEL,
        }
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
