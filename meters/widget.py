#!/usr/bin/env python3
"""Serve the local live weekly Codex pacing widget."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import time


PROJECT = Path(__file__).resolve().parent
PACING_SOURCE = PROJECT.parent / 'codex'
sys.path.insert(0, str(PACING_SOURCE))
import pace  # noqa: E402
import claude_usage
from native_data import claude_readings, model_view


def window_reading(data, minutes):
    now = time.time()
    window = next((w for w in data.get('windows', [])
                   if w['minutes'] == minutes and w['reset'] > now), None)
    if window is None:
        return None
    ideal = pace.ideal(window, now)
    return {'used': window['used'], 'ideal': ideal,
            'leadHours': (window['used'] - ideal) / 100 * minutes / 60,
            'resetsAt': window['reset'], 'stale': bool(data.get('error')) or data.get('complete') is False
            or not 0 <= now-data.get('observed_at', 0) <= pace.MAX_AGE_SEC}


def weekly_reading():
    """Use pace.py's shared cache and its existing Codex app-server reader."""
    data = pace.snapshot(pace.state_dir())
    bucket = dict(data.get('buckets', {}).get('codex') or {})
    bucket['error'] = data.get('error') or bucket.get('error')
    bucket['observed_at'] = data.get('observed_at', 0)
    reading = window_reading(bucket, 10080)
    if reading is None:
        raise RuntimeError(data.get("error") or "Weekly Codex quota is not reported")
    return reading


def claude_reading():
    data = claude_usage.snapshot()
    session = claude_usage.active_session()
    result = {'windows':claude_readings(data,time.time()), 'problem':claude_usage.problem(data,time.time())}
    view = model_view(result,session.get('model'))
    return dict(view['windows'], model=session.get('model'), summary=view['summary'])


def demo_readings():
    """Synthetic display fixtures; no provider calls or persistent state."""
    now = time.time()
    def row(used, lead, hours):
        return {'used': used, 'ideal': used - lead / hours * 100,
                'leadHours': lead, 'resetsAt': now + hours * 1800,
                'stale': False, 'label': '5H session' if hours == 5 else '7D weekly'}
    return {'codex': row(54, 6.72, 168),
            'claude': {'weekly': row(48, -3.36, 168),
                       'session': row(32, -0.9, 5),
                       'model': 'demo · synthetic', 'summary': ''}}


class WidgetHandler(BaseHTTPRequestHandler):
    demo = False
    def do_GET(self):
        port = self.server.server_address[1]
        host = self.headers.get('Host', '').lower()
        if host not in (f'127.0.0.1:{port}', f'localhost:{port}'):
            self.send_error(403, 'Local host required')
            return
        if self.path in ("/", "/index.html"):
            body = (PROJECT / "index.html").read_bytes()
            if self.demo:
                body = body.replace(b'<body>', b'<body><p style="text-align:center;color:#f3c54f">DEMO - synthetic readings - no account access</p>')
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        elif self.path in ("/api/pace", "/api/claude"):
            try:
                payload = (demo_readings()['codex' if self.path == '/api/pace' else 'claude']
                           if self.demo else weekly_reading() if self.path == '/api/pace' else claude_reading())
                body = json.dumps(payload).encode()
                self.send_response(200)
            except (RuntimeError, OSError, ValueError):
                body = json.dumps({"error": "Quota unavailable"}).encode()
                self.send_response(503)
            self.send_header("Content-Type", "application/json")
        else:
            self.send_error(404)
            return
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="Use synthetic readings; never contact accounts.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--once", action="store_true", help="Print one live reading as JSON.")
    args = parser.parse_args()
    if args.once:
        print(json.dumps(demo_readings() if args.demo else {'codex': weekly_reading(), 'claude': claude_reading()}, indent=2))
        return
    WidgetHandler.demo = args.demo
    server = ThreadingHTTPServer(("127.0.0.1", args.port), WidgetHandler)
    print(f"LLM pacing widget: http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
