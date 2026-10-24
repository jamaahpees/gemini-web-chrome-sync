"""Private, single-profile cookie bridge. Python 3.10+, standard library only."""
import argparse
import collections
from contextlib import contextmanager
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

MAX_BODY = 65536
DOMAINS = {'.google.com', 'google.com', '.gemini.google.com', 'gemini.google.com'}
NAMES = {'SID', 'HSID', 'SSID', 'APISID', 'SAPISID', 'SIDCC',
         '__Secure-1PSID', '__Secure-3PSID', '__Secure-1PAPISID', '__Secure-3PAPISID',
         '__Secure-1PSIDTS', '__Secure-3PSIDTS', '__Secure-1PSIDCC', '__Secure-3PSIDCC',
         '__Secure-1PSIDRTS', '__Secure-3PSIDRTS', '__Secure-ENID',
         'AEC', 'NID', 'SOCS', 'COMPASS', '__Secure-BUCKET'}
REQUIRED = {'__Secure-1PSID', '__Secure-1PSIDTS'}


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class Failure(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Backend:
    def __init__(self, config):
        self.config = config
        self.opener = build_opener(NoRedirect(), ProxyHandler({}))

    def request(self, method, suffix='', body=None):
        return self._request(method, '/admin/api/cookies' + suffix, body)

    def _request(self, method, path, body=None):
        headers = {'Authorization': 'Bearer ' + self.config['adminToken'],
                   'x-api-key': self.config['apiKey'], 'Content-Type': 'application/json'}
        req = Request(self.config['url'].rstrip('/') + path,
                      data=None if body is None else json.dumps(body).encode(),
                      headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=10) as response:
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise Failure(503, 'backend_unavailable')
                return json.loads(raw) if raw else {}
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            raise Failure(503, 'backend_unavailable') from None

    def _runtime_config(self):
        data = self._request('GET', '/admin/api/config')
        config = data.get('config') if isinstance(data, dict) else None
        if (not isinstance(config, dict)
                or any(type(config.get(key)) is not bool for key in
                       ('auto_delete_conversation', 'multi_turn', 'temporary_chat'))):
            raise Failure(503, 'backend_unavailable')
        return config

    def _cleanup_settings(self, config):
        return {'enabled': config['auto_delete_conversation'], 'multiTurn': config['multi_turn'],
                'temporaryChat': config['temporary_chat'], 'bestEffort': True}

    def cleanup_settings(self):
        return self._cleanup_settings(self._runtime_config())

    def set_cleanup(self, enabled):
        if type(enabled) is not bool:
            raise Failure(400, 'invalid_cleanup')
        config = self._runtime_config()
        if enabled and (config['multi_turn'] or config['temporary_chat']):
            raise Failure(409, 'cleanup_conflict')
        config['auto_delete_conversation'] = enabled
        self._request('PUT', '/admin/api/config', config)
        verified = self._runtime_config()
        if json.dumps(verified, sort_keys=True) != json.dumps(config, sort_keys=True):
            raise Failure(503, 'backend_partial_failure')
        return self._cleanup_settings(verified)

    def rows(self):
        data = self.request('GET')
        if isinstance(data, dict):
            data = data.get('items', data.get('cookies', data.get('data')))
        if not isinstance(data, list) or any(not isinstance(r, dict) or 'id' not in r for r in data):
            raise Failure(503, 'backend_unavailable')
        return data

    def path(self, row):
        return '/' + quote(str(row['id']), safe='')

    def check(self, row):
        result = self.request('POST', self.path(row) + '/check', {})
        return isinstance(result, dict) and (result.get('ok') is True or result.get('valid') is True
                                             or result.get('status') in ('ok', 'valid'))

    def status(self, row, status):
        self.request('PATCH', self.path(row), {'status': status})
        found = next((r for r in self.rows() if str(r['id']) == str(row['id'])), None)
        if found is None or found.get('status') != status:
            raise Failure(503, 'backend_partial_failure')

    def delete(self, row):
        self.request('DELETE', self.path(row))
        if any(str(r['id']) == str(row['id']) for r in self.rows()):
            raise Failure(503, 'backend_partial_failure')

    def rotate(self, cookie):
        old = self.rows()
        candidate = None
        disabled = []
        committed = False
        try:
            result = self.request('POST', body={'cookie': cookie, 'status': 'disabled'})
            candidate = result.get('data', result) if isinstance(result, dict) else None
            if not isinstance(candidate, dict) or 'id' not in candidate:
                raise Failure(503, 'backend_partial_failure')
            if any(str(r['id']) == str(candidate['id']) for r in old):
                candidate = None
                raise Failure(503, 'backend_partial_failure')
            self.status(candidate, 'disabled')
            if not self.check(candidate):
                self.delete(candidate)
                candidate = None
                return 'invalid'
            for row in old:
                if row.get('status') != 'disabled':
                    disabled.append(row)
                    self.status(row, 'disabled')
            self.status(candidate, 'enabled')
            committed = True
            for row in old:
                self.delete(row)
            return 'applied'
        except Failure as error:
            if committed:
                raise Failure(503, 'backend_partial_failure') from None
            rollback_ok = True
            for row in disabled:
                try:
                    self.status(row, 'enabled')
                except Failure:
                    rollback_ok = False
            if candidate is not None:
                try:
                    self.delete(candidate)
                except Failure:
                    rollback_ok = False
            raise Failure(503, error.message if rollback_ok else 'backend_partial_failure') from None


def validate_snapshot(body):
    if not isinstance(body, dict) or set(body) != {'sequence', 'cookies', 'logout'}:
        raise Failure(400, 'invalid_snapshot')
    sequence, cookies, logout = body['sequence'], body['cookies'], body['logout']
    if type(sequence) is not int or not 1 <= sequence <= 9223372036854775807 or type(logout) is not bool:
        raise Failure(400, 'invalid_snapshot')
    if not isinstance(cookies, list) or len(cookies) > 64:
        raise Failure(400, 'invalid_cookies')
    identities, values = set(), {}
    normalized = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            raise Failure(400, 'invalid_cookies')
        name, value, domain = cookie.get('name'), cookie.get('value'), cookie.get('domain')
        if (not isinstance(name, str) or name not in NAMES or not isinstance(value, str)
                or not isinstance(domain, str) or domain not in DOMAINS
                or cookie.get('path') != '/' or type(cookie.get('secure')) is not bool
                or type(cookie.get('httpOnly')) is not bool
                or (name.startswith('__Secure-') and cookie['secure'] is not True)
                or any(ord(c) < 32 or ord(c) > 126 or c == ';' for c in value)):
            raise Failure(400, 'invalid_cookies')
        identity = (name, domain, '/')
        if identity in identities or (name in values and values[name] != value):
            raise Failure(400, 'duplicate_cookie')
        identities.add(identity)
        values[name] = value
        normalized.append([name, domain, '/', value])
    if logout and cookies:
        raise Failure(400, 'invalid_logout')
    if not logout and (not REQUIRED <= values.keys() or any(not values[n] for n in REQUIRED)):
        raise Failure(400, 'missing_cookies')
    canonical = json.dumps([logout, sorted(normalized)], separators=(',', ':'))
    if len(canonical.encode()) > MAX_BODY:
        raise Failure(413, 'body_too_large')
    return sequence, digest(canonical), values, logout


def load_config(path):
    path = Path(path)
    if os.name != 'nt' and path.stat().st_mode & 0o077:
        raise ValueError('config must have mode 0600')
    config = json.loads(path.read_text())
    targets = config.get('backends', {})
    if not targets:
        raise ValueError('backends required')
    urls = set()
    for target in targets.values():
        parsed = urlsplit(target['url'])
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
                or parsed.username or parsed.password or parsed.path not in ('', '/')
                or parsed.query or parsed.fragment or not parsed.port):
            raise ValueError('backend must be fixed loopback HTTP origin')
        identity = parsed.port
        if identity in urls:
            raise ValueError('each backend must have a dedicated port')
        urls.add(identity)
        if not all(isinstance(target.get(k), str) and target[k] for k in ('adminToken', 'apiKey')):
            raise ValueError('backend credentials required')
    return config


_MIGRATION_COLUMNS = [
    ('last_seen', 'INTEGER DEFAULT 0'),
    ('last_applied_at', 'INTEGER DEFAULT 0'),
    ('refresh_enabled', 'INTEGER DEFAULT 0'),
    ('refresh_minutes', 'INTEGER DEFAULT 0'),
    ('last_refresh_at', 'INTEGER DEFAULT 0'),
    ('next_refresh_at', 'INTEGER DEFAULT 0'),
]


class Store:
    def __init__(self, directory, config, backend_factory=Backend):
        directory = Path(directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != 'nt':
            os.chmod(directory, 0o700)
        self.path = directory / 'state.sqlite3'
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        if os.name != 'nt':
            os.chmod(self.path, 0o600)
        self.config, self.factory = config, backend_factory
        self.lock = threading.RLock()
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS codes(hash TEXT PRIMARY KEY, backend TEXT, expires REAL);
                CREATE TABLE IF NOT EXISTS devices(
                  id TEXT PRIMARY KEY, token TEXT UNIQUE, backend TEXT UNIQUE, revoked INTEGER DEFAULT 0,
                  account TEXT, applied INTEGER DEFAULT 0, applied_hash TEXT, seen INTEGER DEFAULT 0,
                  seen_hash TEXT, status TEXT DEFAULT 'awaiting_sync', requested INTEGER DEFAULT 1,
                  checked REAL DEFAULT 0, inflight INTEGER DEFAULT 0);
            ''')
            existing = {r[1] for r in db.execute('PRAGMA table_info(devices)').fetchall()}
            for col, defn in _MIGRATION_COLUMNS:
                if col not in existing:
                    db.execute(f'ALTER TABLE devices ADD COLUMN {col} {defn}')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_code(self, backend):
        if backend not in self.config['backends']:
            raise Failure(400, 'unknown_backend')
        code = secrets.token_urlsafe(24)
        with self.lock, self.db() as db:
            if db.execute('SELECT 1 FROM devices WHERE backend=?', (backend,)).fetchone():
                raise Failure(409, 'target_already_bound')
            db.execute('DELETE FROM codes WHERE expires < ? OR backend=?', (time.time(), backend))
            db.execute('INSERT INTO codes VALUES(?,?,?)', (digest(code), backend, time.time() + 600))
        return code

    def pair(self, code):
        if not isinstance(code, str) or len(code) > 256:
            raise Failure(401, 'invalid_pairing')
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM codes WHERE hash=? AND expires>?', (digest(code), time.time())).fetchone()
            if not row:
                raise Failure(401, 'invalid_pairing')
            if db.execute('SELECT 1 FROM devices WHERE backend=?', (row['backend'],)).fetchone():
                raise Failure(409, 'target_already_bound')
            device, token = secrets.token_urlsafe(18), secrets.token_urlsafe(32)
            db.execute('INSERT INTO devices(id,token,backend) VALUES(?,?,?)', (device, digest(token), row['backend']))
            db.execute('DELETE FROM codes WHERE backend=?', (row['backend'],))
        return {'deviceId': device, 'token': token}

    def auth(self, db, token):
        if not isinstance(token, str) or not 20 <= len(token) <= 256:
            raise Failure(401, 'unauthorized')
        row = db.execute('SELECT * FROM devices WHERE token=? AND revoked=0', (digest(token),)).fetchone()
        if not row:
            raise Failure(401, 'unauthorized')
        return row

    def disable_target(self, row):
        backend = self.factory(self.config['backends'][row['backend']])
        for item in backend.rows():
            backend.status(item, 'disabled')
        if any(item.get('status') != 'disabled' for item in backend.rows()):
            raise Failure(503, 'backend_partial_failure')

    def revoke(self, device):
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM devices WHERE id=?', (device,)).fetchone()
            if row is None:
                raise Failure(404, 'unknown_device')
            self.disable_target(row)
            db.execute('UPDATE devices SET revoked=1,token=NULL WHERE id=?', (device,))

    def authenticated_revoke(self, token):
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.auth(db, token)
            self.disable_target(row)
            db.execute('UPDATE devices SET revoked=1,token=NULL WHERE id=?', (row['id'],))
        return {'status': 'revoked'}

    def snapshot(self, token, body):
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.auth(db, token)
            seq, hashed, values, logout = validate_snapshot(body)
            if seq < row['seen'] or (seq == row['seen'] and hashed != row['seen_hash']):
                raise Failure(409, 'stale_sequence')
            if seq == row['applied'] and hmac.compare_digest(hashed, row['applied_hash'] or ''):
                return {'status': 'unchanged', 'sequence': seq}
            account = digest(values['__Secure-1PSID']) if not logout else row['account']
            if row['account'] and account != row['account']:
                return {'status': 'account_changed', 'sequence': seq}
            db.execute('UPDATE devices SET seen=?,seen_hash=?,inflight=1 WHERE id=?', (seq, hashed, row['id']))
            db.commit()
            db.execute('BEGIN IMMEDIATE')
            # Re-authenticate after durable journal commit: a local CLI may have revoked the device.
            row = self.auth(db, token)
            backend = self.factory(self.config['backends'][row['backend']])
            try:
                if logout:
                    for item in backend.rows():
                        backend.status(item, 'disabled')
                    result = 'applied'
                else:
                    result = backend.rotate('; '.join(k + '=' + v for k, v in sorted(values.items())))
                if result == 'invalid':
                    db.execute("UPDATE devices SET status='invalid',requested=1,inflight=0 WHERE id=?", (row['id'],))
                else:
                    db.execute('''UPDATE devices SET applied=?,applied_hash=?,account=?,status=?,
                               requested=0,checked=?,inflight=0,last_applied_at=? WHERE id=?''',
                               (seq, hashed, account, 'logged_out' if logout else 'good', time.time(),
                                int(time.time()), row['id']))
                db.commit()
                return {'status': result, 'sequence': seq}
            except Failure as error:
                db.execute('UPDATE devices SET status=?,requested=1,inflight=0 WHERE id=?', (error.message, row['id']))
                db.commit()
                raise

    def status(self, token):
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.auth(db, token)
            status, requested = row['status'], bool(row['requested'])
            if row['inflight']:
                status, requested = 'recovery_required', True
            elif (row['applied'] and status not in ('logged_out', 'backend_partial_failure')
                  and time.time() - row['checked'] >= 300):
                backend = self.factory(self.config['backends'][row['backend']])
                try:
                    rows = [r for r in backend.rows() if r.get('status') == 'enabled']
                    good = len(rows) == 1 and backend.check(rows[0])
                    status, requested = ('good', False) if good else ('invalid', True)
                except Failure:
                    status, requested = 'backend_unavailable', True
                db.execute('UPDATE devices SET status=?,requested=?,checked=? WHERE id=?',
                           (status, int(requested), time.time(), row['id']))
            db.execute('UPDATE devices SET last_seen=? WHERE id=?', (int(time.time()), row['id']))
            return {'syncRequested': requested, 'status': status, 'lastApplied': row['applied']}

    def cleanup_settings(self, token):
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.auth(db, token)
            backend = self.factory(self.config['backends'][row['backend']])
            return backend.cleanup_settings()

    def set_cleanup(self, token, body):
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.auth(db, token)
            if (not isinstance(body, dict) or set(body) != {'enabled'}
                    or type(body['enabled']) is not bool):
                raise Failure(400, 'invalid_cleanup')
            backend = self.factory(self.config['backends'][row['backend']])
            return backend.set_cleanup(body['enabled'])

    def heartbeat(self, token, body):
        if not isinstance(body, dict) or set(body) != {'refresh'}:
            raise Failure(400, 'invalid_heartbeat')
        ref = body['refresh']
        if not isinstance(ref, dict) or set(ref) != {'enabled', 'minutes', 'lastRefreshAt', 'nextRefreshAt'}:
            raise Failure(400, 'invalid_heartbeat')
        enabled = ref['enabled']
        minutes = ref['minutes']
        last_refresh_at = ref['lastRefreshAt']
        next_refresh_at = ref['nextRefreshAt']
        if type(enabled) is not bool:
            raise Failure(400, 'invalid_heartbeat')
        if type(minutes) is not int or not (15 <= minutes <= 1440):
            raise Failure(400, 'invalid_heartbeat')
        if type(last_refresh_at) is not int or last_refresh_at < 0:
            raise Failure(400, 'invalid_heartbeat')
        if type(next_refresh_at) is not int or next_refresh_at < 0:
            raise Failure(400, 'invalid_heartbeat')
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.auth(db, token)
            db.execute('''UPDATE devices SET last_seen=?,refresh_enabled=?,refresh_minutes=?,
                          last_refresh_at=?,next_refresh_at=? WHERE id=?''',
                       (int(time.time()), int(enabled), minutes,
                        last_refresh_at, next_refresh_at, row['id']))
        return {'status': 'ok'}

    def profiles(self, token):
        _ONLINE_THRESHOLD = 180
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            caller = self.auth(db, token)
            rows = db.execute(
                'SELECT * FROM devices WHERE revoked=0'
            ).fetchall()
            now = int(time.time())
            result = []
            for r in rows:
                backend_name = r['backend']
                backend_cfg = self.config['backends'].get(backend_name, {})
                raw_label = backend_cfg.get('label') or backend_name
                label = raw_label[:80]
                last_seen = r['last_seen'] or 0
                online = (now - last_seen) <= _ONLINE_THRESHOLD
                result.append({
                    'profileId': r['id'],
                    'label': label,
                    'isCurrent': r['id'] == caller['id'],
                    'online': online,
                    'lastSeen': last_seen,
                    'lastAppliedAt': r['last_applied_at'] or 0,
                    'status': r['status'],
                    'refresh': {
                        'enabled': bool(r['refresh_enabled']),
                        'minutes': r['refresh_minutes'] or 0,
                        'lastRefreshAt': r['last_refresh_at'] or 0,
                        'nextRefreshAt': r['next_refresh_at'] or 0,
                    },
                })
            return {'profiles': result}


def validate_bind(value):
    message = 'bind must be 127.0.0.1 or an IPv4 address in 100.64.0.0/10'
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        raise ValueError(message) from None
    if str(address) != value or (value != '127.0.0.1'
                                and address not in ipaddress.IPv4Network('100.64.0.0/10')):
        raise ValueError(message)
    return value


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, store):
        validate_bind(address[0])
        self.store = store
        self.slots = threading.BoundedSemaphore(16)
        self.rates = collections.OrderedDict()
        self.rate_lock = threading.Lock()
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        pass

    def pairing_limit(self, source):
        with self.rate_lock:
            now = time.monotonic()
            entries = [t for t in self.rates.pop(source, []) if now - t < 600]
            allowed = len(entries) < 10
            entries.append(now)
            self.rates[source] = entries[-10:]
            while len(self.rates) > 1024:
                self.rates.popitem(last=False)
            if not allowed:
                raise Failure(429, 'rate_limited')


class Handler(BaseHTTPRequestHandler):
    server_version = 'CookieSync'
    sys_version = ''
    protocol_version = 'HTTP/1.0'

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self.reply(code, {'error': 'request_rejected'})

    def reply(self, code, body):
        raw = json.dumps(body, separators=(',', ':')).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True

    def dispatch(self):
        try:
            hosts = self.headers.get_all('Host', [])
            allowed = {'127.0.0.1', 'localhost', '[::1]'} | set(self.server.store.config.get('allowedHosts', []))
            host = hosts[0].lower() if len(hosts) == 1 else ''
            # Only an optional numeric port is accepted, never userinfo or arbitrary suffixes.
            base = host.rsplit(':', 1)[0] if ':' in host and host.rsplit(':', 1)[1].isdigit() else host
            if base not in allowed:
                raise Failure(400, 'invalid_host')
            if (self.command, self.path) not in {('POST', '/v1/pair'), ('DELETE', '/v1/pair'),
                                               ('GET', '/v1/status'), ('POST', '/v1/snapshot'),
                                               ('POST', '/v1/heartbeat'), ('GET', '/v1/profiles'),
                                               ('GET', '/v1/cleanup'), ('POST', '/v1/cleanup')}:
                raise Failure(404, 'not_found')
            if self.headers.get('Transfer-Encoding') or self.headers.get('Expect'):
                raise Failure(400, 'invalid_framing')
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) > 1 or (lengths and not lengths[0].isdigit()):
                raise Failure(400, 'invalid_framing')
            size = int(lengths[0]) if lengths else 0
            if size > MAX_BODY:
                raise Failure(413, 'body_too_large')
            if self.command != 'POST' and size:
                raise Failure(400, 'unexpected_body')
            body = None
            if self.command == 'POST':
                if not lengths:
                    raise Failure(411, 'length_required')
                if self.headers.get_content_type() != 'application/json':
                    raise Failure(415, 'json_required')
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise Failure(400, 'invalid_body')
                try:
                    body = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise Failure(400, 'invalid_json') from None
            store = self.server.store
            if self.path == '/v1/pair' and self.command == 'POST':
                self.server.pairing_limit(self.client_address[0])
                if not isinstance(body, dict) or set(body) != {'code'}:
                    raise Failure(400, 'invalid_pairing')
                result = store.pair(body['code'])
            else:
                auth = self.headers.get_all('Authorization', [])
                if len(auth) != 1 or not auth[0].startswith('Bearer '):
                    raise Failure(401, 'unauthorized')
                token = auth[0][7:]
                if self.command == 'DELETE':
                    result = store.authenticated_revoke(token)
                elif self.path == '/v1/status':
                    result = store.status(token)
                elif self.path == '/v1/profiles':
                    result = store.profiles(token)
                elif self.path == '/v1/cleanup':
                    result = (store.cleanup_settings(token) if self.command == 'GET'
                              else store.set_cleanup(token, body))
                elif self.path == '/v1/heartbeat':
                    result = store.heartbeat(token, body)
                else:
                    result = store.snapshot(token, body)
            self.reply(200, result)
        except Failure as error:
            self.reply(error.status, {'error': error.message})
        except (TimeoutError, socket.timeout):
            self.reply(408, {'error': 'request_timeout'})
        except Exception:
            self.reply(500, {'error': 'internal_error'})

    do_GET = do_POST = do_DELETE = dispatch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--state', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    serve = sub.add_parser('serve')
    serve.add_argument('--port', type=int, default=8787)
    serve.add_argument('--bind', type=validate_bind, default='127.0.0.1',
                       help='listen on 127.0.0.1 (default) or a Tailscale IPv4 address in 100.64.0.0/10')
    create = sub.add_parser('create-pairing')
    create.add_argument('backend')
    sub.add_parser('list')
    revoke = sub.add_parser('revoke')
    revoke.add_argument('device')
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        store = Store(args.state, config)
        if args.command == 'serve':
            # Lifetime process lock prevents two services from interleaving backend network operations.
            if os.name != 'nt':
                import fcntl
                lock = open(Path(args.state) / 'service.lock', 'a')
                os.chmod(lock.name, 0o600)
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            Server((args.bind, args.port), store).serve_forever()
        elif args.command == 'create-pairing':
            print(store.create_code(args.backend))
        elif args.command == 'revoke':
            store.revoke(args.device)
        else:
            with store.db() as db:
                print(json.dumps([dict(r) for r in db.execute(
                    'SELECT id,backend,revoked,applied,status FROM devices')], indent=2))
    except (Failure, ValueError, OSError, KeyError, sqlite3.Error):
        parser.exit(1, 'Operation failed; check local configuration, ownership, and target binding.\n')


if __name__ == '__main__':
    main()
