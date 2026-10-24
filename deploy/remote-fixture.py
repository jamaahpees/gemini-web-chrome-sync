"""TEST ONLY: synthetic cookie-admin backend; never point a production target here.

Run transiently as root on the VPS after configuring the separate e2e-test target.
Cookie values stay in process memory. Only counters/status metadata go to disk.
"""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import signal
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/etc/gemini-cookie-sync/config.json')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    target = config['backends']['e2e-test']
    if target['url'].rstrip('/') != 'http://127.0.0.1:8099':
        raise SystemExit('e2e-test must point exclusively at localhost:8099')
    if any(name != 'e2e-test' and value['url'].rstrip('/') == target['url'].rstrip('/')
           for name, value in config['backends'].items()):
        raise SystemExit('Fixture backend must not be shared with another target')
    result = Path('/var/lib/gemini-cookie-sync-e2e/result.json')
    result.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(result.parent, 0o700)
    rows = {}
    counters = {'testOnly': True, 'creates': 0, 'checks': 0, 'enables': 0,
                'disables': 0, 'deletes': 0, 'rejected': 0}
    next_id = 0

    def persist():
        metadata = dict(counters, rows=len(rows),
                        enabled=sum(r['status'] == 'enabled' for r in rows.values()),
                        disabled=sum(r['status'] == 'disabled' for r in rows.values()))
        temporary = result.with_suffix('.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(metadata, stream)
        os.replace(temporary, result)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *unused):
            pass

        def reply(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def handle_request(self):
            nonlocal next_id
            self.connection.settimeout(10)
            if not (hmac.compare_digest(self.headers.get('Authorization', ''),
                                        'Bearer ' + target['adminToken'])
                    and hmac.compare_digest(self.headers.get('x-api-key', ''), target['apiKey'])):
                return self.reply(401, {'error': 'unauthorized'})
            parts = urlsplit(self.path).path.strip('/').split('/')
            if parts[:3] != ['admin', 'api', 'cookies']:
                return self.reply(404, {})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if size < 0 or size > 65536:
                    raise ValueError()
                body = json.loads(self.rfile.read(size)) if size else {}
                if self.command == 'GET' and len(parts) == 3:
                    return self.reply(200, {'items': [dict(id=k, status=v['status'])
                                                     for k, v in rows.items()]})
                if self.command == 'POST' and len(parts) == 3:
                    cookie = body.get('cookie', '')
                    pairs = [part.strip().partition('=') for part in cookie.split(';')]
                    if (body.get('status') != 'disabled' or not cookie or
                            any(not sep or not value.startswith('e2e-synthetic-')
                                for name, sep, value in pairs)):
                        counters['rejected'] += 1
                        persist()
                        return self.reply(400, {'error': 'synthetic_disabled_only'})
                    next_id += 1
                    rows[str(next_id)] = {'cookie': cookie, 'status': 'disabled'}
                    counters['creates'] += 1
                    persist()
                    return self.reply(200, {'id': str(next_id), 'status': 'disabled'})
                ident = parts[3] if len(parts) >= 4 else ''
                if ident not in rows:
                    return self.reply(404, {})
                if self.command == 'POST' and parts[4:] == ['check']:
                    counters['checks'] += 1
                    persist()
                    return self.reply(200, {'ok': True})
                if self.command == 'PATCH' and len(parts) == 4:
                    status = body.get('status')
                    if status not in ('enabled', 'disabled'):
                        raise ValueError()
                    rows[ident]['status'] = status
                    counters['enables' if status == 'enabled' else 'disables'] += 1
                elif self.command == 'DELETE' and len(parts) == 4:
                    del rows[ident]
                    counters['deletes'] += 1
                else:
                    return self.reply(405, {})
                persist()
                return self.reply(200, {'ok': True})
            except (ValueError, TypeError, KeyError):
                return self.reply(400, {'error': 'invalid_request'})

        do_GET = do_POST = do_PATCH = do_DELETE = handle_request

    persist()
    server = HTTPServer(('127.0.0.1', 8099), Handler)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        rows.clear()
        persist()


if __name__ == '__main__':
    main()
