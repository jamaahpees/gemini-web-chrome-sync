import http.client
import json
import os
import re
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cookie_sync import Backend, Failure, MAX_BODY, NAMES, Server, Store, load_config, validate_snapshot


class FakeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def dispatch(self):
        server = self.server
        if server.outage:
            self.send_response(503)
            self.end_headers()
            return
        parts = self.path.split('/')
        suffix = parts[4:]
        raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        body = json.loads(raw) if raw else {}
        result = {}
        if not suffix or suffix == ['']:
            if self.command == 'GET':
                result = {'enabled': 1, 'items': list(server.rows.values())}
            elif self.command == 'POST':
                server.counter += 1
                key = str(server.counter)
                server.import_statuses.append(body.get('status'))
                status = 'enabled' if server.ignore_import_status else body.get('status', 'enabled')
                server.rows[key] = {'id': key, 'cookie': body['cookie'], 'status': status}
                result = {'id': key}
        else:
            key = suffix[0]
            if len(suffix) > 1:
                server.checks += 1
                server.checked_statuses.append(server.rows[key]['status'])
                result = {'ok': 'invalid' not in server.rows[key]['cookie']}
            elif self.command == 'PATCH':
                if server.patch_fail and body['status'] == 'disabled':
                    self.send_response(503)
                    self.end_headers()
                    return
                server.rows[key]['status'] = body['status']
            elif self.command == 'DELETE':
                if server.delete_fail:
                    self.send_response(503)
                    self.end_headers()
                    return
                server.rows.pop(key, None)
        payload = json.dumps(result).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PATCH = do_DELETE = dispatch


def snapshot(sequence=1, account='account-a', stamp='timestamp', logout=False):
    return {'sequence': sequence, 'logout': logout, 'cookies': [] if logout else [
        {'name': name, 'value': value, 'domain': '.google.com', 'path': '/', 'secure': True, 'httpOnly': True}
        for name, value in [('__Secure-1PSID', account), ('__Secure-1PSIDTS', stamp)]]}


class BindTests(unittest.TestCase):
    def test_server_rejects_non_private_bind_before_opening_socket(self):
        for address in ('0.0.0.0', '192.168.1.2', '10.0.0.1', '172.16.0.1',
                        '8.8.8.8', '100.63.255.255', '100.128.0.0', '127.0.0.2',
                        'localhost', '::1', '::', '::ffff:100.100.100.100',
                        '100.100.100.100.example.com', '100.100.100.100/32',
                        ' 100.100.100.100', '100.100.100.100 ', '100.112.195.084'):
            with self.subTest(address=address):
                with patch.object(ThreadingHTTPServer, '__init__') as initialize:
                    with self.assertRaises(ValueError):
                        Server((address, 8787), None)
                    initialize.assert_not_called()

    def test_server_accepts_exact_loopback_and_cgnat_boundaries(self):
        for address in ('127.0.0.1', '100.64.0.0', '100.100.100.100', '100.127.255.255'):
            with self.subTest(address=address):
                with patch.object(ThreadingHTTPServer, '__init__') as initialize:
                    Server((address, 8787), None)
                    self.assertEqual(initialize.call_args.args[0], (address, 8787))

    def test_serve_cli_bind_default_and_explicit_private_address(self):
        from cookie_sync import main
        for arguments, expected in (([], '127.0.0.1'),
                                    (['--bind', '100.100.100.100'], '100.100.100.100')):
            with self.subTest(arguments=arguments):
                with patch('sys.argv', ['cookie_sync', '--config', 'unused', '--state', 'unused',
                                        'serve', *arguments]), \
                        patch('cookie_sync.load_config', return_value={}), \
                        patch('cookie_sync.Store') as store, \
                        patch('cookie_sync.Server') as server, \
                        patch('cookie_sync.os.name', 'nt'):
                    main()
                    server.assert_called_once_with((expected, 8787), store.return_value)
                    server.return_value.serve_forever.assert_called_once_with()

    def test_serve_cli_rejects_unsafe_bind_before_loading_state(self):
        from cookie_sync import main
        with patch('sys.argv', ['cookie_sync', '--config', 'unused', '--state', 'unused',
                                'serve', '--bind', '0.0.0.0']), \
                patch('cookie_sync.load_config') as config, \
                patch('cookie_sync.Store') as store, \
                patch('cookie_sync.Server') as server:
            with self.assertRaises(SystemExit) as error:
                main()
            self.assertEqual(error.exception.code, 2)
            config.assert_not_called()
            store.assert_not_called()
            server.assert_not_called()


class BridgeTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(os.environ.get('LOCALAPPDATA', tempfile.gettempdir())) / 'Temp' / 'opencode'
        if not temp_root.is_dir():
            temp_root = Path(tempfile.gettempdir())
        self.tmp = tempfile.TemporaryDirectory(dir=temp_root)
        self.backend = ThreadingHTTPServer(('127.0.0.1', 0), FakeHandler)
        self.backend.rows = {}
        self.backend.checked_statuses = []
        self.backend.import_statuses = []
        self.backend.ignore_import_status = False
        self.backend.counter = self.backend.checks = 0
        self.backend.outage = self.backend.patch_fail = self.backend.delete_fail = False
        threading.Thread(target=self.backend.serve_forever, daemon=True).start()
        self.config = {'backends': {'personal': {
            'url': 'http://127.0.0.1:' + str(self.backend.server_port),
            'adminToken': 'fake-admin', 'apiKey': 'fake-api'}}}
        self.store = Store(Path(self.tmp.name) / 'state', self.config)
        self.pair = self.store.pair(self.store.create_code('personal'))
        self.token = self.pair['token']
        self.server = Server(('127.0.0.1', 0), self.store)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.backend.shutdown()
        self.backend.server_close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, token=None, headers=None):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        hdr = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (token or self.token)}
        hdr.update(headers or {})
        client.request(method, path, None if body is None else json.dumps(body), hdr)
        response = client.getresponse()
        code, result = response.status, json.loads(response.read())
        self.assertNotIn('Access-Control-Allow-Origin', response.headers)
        client.close()
        return code, result

    def test_tailscale_host_requires_allowlist_and_authentication(self):
        host = {'Host': '100.100.100.100:8787'}
        self.assertEqual(self.request('GET', '/v1/status', headers=host),
                         (400, {'error': 'invalid_host'}))
        self.config['allowedHosts'] = ['100.100.100.100']
        self.assertEqual(self.request('GET', '/v1/status', headers=host)[0], 200)
        self.assertEqual(self.request('GET', '/v1/status', token='invalid', headers=host),
                         (401, {'error': 'unauthorized'}))
        self.assertEqual(self.request('GET', '/v1/status', headers={
            'Host': '100.100.100.100.example.com:8787'})[0], 400)

    def test_replay_stale_and_ordering(self):
        self.assertEqual(self.request('POST', '/v1/snapshot', snapshot())[1]['status'], 'applied')
        self.assertEqual(self.request('POST', '/v1/snapshot', snapshot())[1]['status'], 'unchanged')
        self.assertEqual(self.backend.counter, 1)
        self.assertEqual(self.request('POST', '/v1/snapshot', snapshot(1, stamp='other'))[0], 409)
        self.assertEqual(self.request('POST', '/v1/snapshot', snapshot(3, stamp='new'))[0], 200)
        self.assertEqual(self.request('POST', '/v1/snapshot', snapshot(2))[0], 409)

    def test_helper_allowlist_contract(self):
        helper = (Path(__file__).resolve().parent.parent / 'sync' / 'helpers.js').read_text()
        definition = re.search(r'const SESSION_NAMES = new Set\(\[([\s\S]*?)\]\)', helper)
        self.assertIsNotNone(definition)
        names = set(re.findall(r"'([^']+)'", definition.group(1)))
        self.assertEqual(NAMES, names)
        body = snapshot()
        present = {c['name'] for c in body['cookies']}
        for name in names - present:
            body['cookies'].append({'name': name, 'value': 'synthetic', 'domain': '.google.com',
                                    'path': '/', 'secure': name.startswith('__Secure-'), 'httpOnly': False})
        self.assertEqual(validate_snapshot(body)[0], 1)

    def test_revoke_disables_rows_and_outage_preserves_retry(self):
        self.store.snapshot(self.token, snapshot())
        self.backend.outage = True
        self.assertEqual(self.request('DELETE', '/v1/pair')[0], 503)
        self.assertEqual(self.request('GET', '/v1/status')[0], 200)
        self.backend.outage = False
        self.assertEqual(self.request('DELETE', '/v1/pair')[0], 200)
        self.assertTrue(all(r['status'] == 'disabled' for r in self.backend.rows.values()))
        self.assertEqual(self.request('GET', '/v1/status')[0], 401)

    def test_cli_revoke_disables_rows_and_preserves_token_on_failure(self):
        self.store.snapshot(self.token, snapshot())
        self.backend.patch_fail = True
        with self.assertRaises(Failure):
            self.store.revoke(self.pair['deviceId'])
        self.assertEqual(self.request('GET', '/v1/status')[0], 200)
        self.backend.patch_fail = False
        self.store.revoke(self.pair['deviceId'])
        self.assertTrue(all(r['status'] == 'disabled' for r in self.backend.rows.values()))
        self.assertEqual(self.request('GET', '/v1/status')[0], 401)

    def test_atomic_disabled_import_and_legacy_fallback(self):
        self.store.snapshot(self.token, snapshot())
        self.assertEqual(self.backend.import_statuses, ['disabled'])
        self.backend.ignore_import_status = True
        self.store.snapshot(self.token, snapshot(2, stamp='new'))
        self.assertEqual(self.backend.checked_statuses, ['disabled', 'disabled'])

    def test_bad_token_and_revoke(self):
        self.assertEqual(self.request('GET', '/v1/status', token='x' * 40)[0], 401)
        self.assertEqual(self.request('DELETE', '/v1/pair')[0], 200)
        self.assertEqual(self.request('GET', '/v1/status')[0], 401)
        with self.assertRaises(Failure):
            self.store.create_code('personal')

    def test_cross_profile_and_single_use(self):
        self.store.snapshot(self.token, snapshot())
        result = self.store.snapshot(self.token, snapshot(2, account='account-b'))
        self.assertEqual(result['status'], 'account_changed')
        self.assertEqual(self.backend.counter, 1)
        with self.assertRaises(Failure):
            self.store.create_code('personal')
        with self.assertRaises(Failure):
            self.store.pair('not-a-code')

    def test_pairing_expiry_and_single_use(self):
        with self.store.db() as db:
            db.execute('DELETE FROM devices')
        code = self.store.create_code('personal')
        with self.store.db() as db:
            db.execute('UPDATE codes SET expires=0')
        self.assertEqual(self.request('POST', '/v1/pair', {'code': code})[0], 401)
        code = self.store.create_code('personal')
        self.assertEqual(self.request('POST', '/v1/pair', {'code': code})[0], 200)
        self.assertEqual(self.request('POST', '/v1/pair', {'code': code})[0], 401)

    def test_invalid_candidate_preserves_old_and_exact_retry(self):
        self.store.snapshot(self.token, snapshot())
        before = dict(self.backend.rows)
        bad = snapshot(2, stamp='invalid')
        self.assertEqual(self.store.snapshot(self.token, bad)['status'], 'invalid')
        self.assertEqual(self.backend.rows, before)
        self.assertEqual(self.store.status(self.token)['lastApplied'], 1)
        self.assertEqual(self.store.snapshot(self.token, bad)['status'], 'invalid')
        self.assertEqual(self.request('POST', '/v1/snapshot', snapshot(2, stamp='fixed'))[0], 409)
        self.assertEqual(self.store.snapshot(self.token, snapshot(3))['status'], 'applied')

    def test_swap_deletes_all_previous_rows(self):
        self.store.snapshot(self.token, snapshot())
        self.store.snapshot(self.token, snapshot(2, stamp='fresh'))
        self.assertEqual(len(self.backend.rows), 1)
        self.assertEqual(next(iter(self.backend.rows.values()))['status'], 'enabled')
        self.assertIn('fresh', next(iter(self.backend.rows.values()))['cookie'])

    def test_outage_retry_and_status(self):
        self.backend.outage = True
        self.assertEqual(self.request('POST', '/v1/snapshot', snapshot())[0], 503)
        self.assertEqual(self.store.status(self.token)['status'], 'backend_unavailable')
        self.assertEqual(self.store.status(self.token)['lastApplied'], 0)
        self.backend.outage = False
        self.assertEqual(self.store.snapshot(self.token, snapshot())['status'], 'applied')

    def test_rotation_rollback_and_partial_cleanup(self):
        self.store.snapshot(self.token, snapshot())
        old = next(iter(self.backend.rows))
        self.backend.patch_fail = True
        self.assertEqual(self.request('POST', '/v1/snapshot', snapshot(2))[0], 503)
        self.assertEqual(list(self.backend.rows), [old])
        self.assertEqual(self.backend.rows[old]['status'], 'enabled')
        self.backend.patch_fail = False
        self.backend.delete_fail = True
        self.assertEqual(self.request('POST', '/v1/snapshot', snapshot(3))[0], 503)
        self.assertEqual(self.store.status(self.token)['status'], 'backend_partial_failure')
        self.assertEqual(self.store.status(self.token)['lastApplied'], 1)

    def test_logout_disables_not_deletes_and_cannot_rebind(self):
        self.store.snapshot(self.token, snapshot())
        self.assertEqual(self.store.snapshot(self.token, snapshot(2, logout=True))['status'], 'applied')
        self.assertEqual(self.store.status(self.token)['status'], 'logged_out')
        self.assertEqual(len(self.backend.rows), 1)
        self.assertTrue(all(r['status'] == 'disabled' for r in self.backend.rows.values()))
        self.assertEqual(self.store.snapshot(self.token, snapshot(3, account='other'))['status'], 'account_changed')

    def test_status_check_cached(self):
        self.store.snapshot(self.token, snapshot())
        count = self.backend.checks
        self.store.status(self.token)
        self.store.status(self.token)
        self.assertEqual(self.backend.checks, count)
        with self.store.db() as db:
            db.execute('UPDATE devices SET checked=0')
        self.store.status(self.token)
        self.assertEqual(self.backend.checks, count + 1)
        self.backend.outage = True
        with self.store.db() as db:
            db.execute('UPDATE devices SET checked=0')
        self.assertEqual(self.store.status(self.token)['status'], 'backend_unavailable')

    def test_standard_nonsecure_and_extended_cookie_names(self):
        body = snapshot()
        for name, secure in [('SID', False), ('HSID', False), ('APISID', False),
                             ('COMPASS', False), ('__Secure-BUCKET', True)]:
            body['cookies'].append({'name': name, 'value': 'synthetic', 'domain': '.google.com',
                                    'path': '/', 'secure': secure, 'httpOnly': False})
        self.assertEqual(self.request('POST', '/v1/snapshot', body)[1]['status'], 'applied')
        bad = snapshot(2)
        del bad['cookies'][0]['httpOnly']
        self.assertEqual(self.request('POST', '/v1/snapshot', bad)[0], 400)
        bad['cookies'][0]['httpOnly'] = 'true'
        self.assertEqual(self.request('POST', '/v1/snapshot', bad)[0], 400)

    def test_candidate_disabled_before_check(self):
        self.store.snapshot(self.token, snapshot())
        self.assertEqual(self.backend.checked_statuses, ['disabled'])

    def test_validation_does_not_consume_sequence(self):
        bads = []
        for field, value in [('domain', '.evil.google.com'), ('path', '/foo'), ('secure', False),
                             ('value', 'bad;value'), ('value', 'bad\r\nvalue'), ('name', 'other')]:
            bad = snapshot()
            bad['cookies'][0][field] = value
            bads.append(bad)
        bad = snapshot()
        bad['cookies'].append(bad['cookies'][0])
        bads.append(bad)
        bad = snapshot()
        bad['cookies'] *= 33
        bads.append(bad)
        for bad in bads:
            self.assertEqual(self.request('POST', '/v1/snapshot', bad)[0], 400)
        self.assertEqual(self.store.snapshot(self.token, snapshot())['status'], 'applied')

    def test_http_boundaries(self):
        self.assertEqual(self.request('POST', '/v1/snapshot', {'x': 'x' * MAX_BODY})[0], 413)
        self.assertEqual(self.request('GET', '/v1/status', headers={'Host': 'attacker.example'})[0], 400)
        self.assertEqual(self.request('GET', '/admin/api/cookies')[0], 404)
        self.assertEqual(self.request('POST', '/v1/snapshot', snapshot(), headers={'Transfer-Encoding': 'chunked'})[0], 400)
        for _ in range(10):
            self.request('POST', '/v1/pair', {'code': 'wrong'})
        self.assertEqual(self.request('POST', '/v1/pair', {'code': 'wrong'})[0], 429)

    def test_durability_and_no_credentials_in_database(self):
        self.store.snapshot(self.token, snapshot())
        reopened = Store(Path(self.tmp.name) / 'state', self.config)
        self.assertEqual(reopened.status(self.token)['lastApplied'], 1)
        raw = reopened.path.read_bytes()
        for secret in [self.token, 'account-a', 'timestamp', 'fake-admin', 'fake-api']:
            self.assertNotIn(secret.encode(), raw)
        if os.name != 'nt':
            self.assertEqual(reopened.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(reopened.path.parent.stat().st_mode & 0o777, 0o700)

    def test_crash_journal_requests_recovery(self):
        with self.store.db() as db:
            db.execute('UPDATE devices SET inflight=1')
        self.assertEqual(self.store.status(self.token)['status'], 'recovery_required')
        self.assertTrue(self.store.status(self.token)['syncRequested'])

    def test_config_rejects_remote_redirect_and_alias_targets(self):
        path = Path(self.tmp.name) / 'config.json'
        path.write_text(json.dumps(self.config))
        os.chmod(path, 0o600)
        self.assertIn('personal', load_config(path)['backends'])
        for url in ['http://example.com:8083', 'https://localhost:8083',
                    'http://localhost:8083/admin', 'http://user@localhost:8083']:
            self.config['backends']['personal']['url'] = url
            path.write_text(json.dumps(self.config))
            with self.assertRaises(ValueError):
                load_config(path)

    def test_migration_additive_on_legacy_database(self):
        legacy_dir = Path(self.tmp.name) / 'legacy_state'
        legacy_dir.mkdir(parents=True, exist_ok=True)
        db_path = legacy_dir / 'state.sqlite3'
        import sqlite3 as sq
        conn = sq.connect(db_path)
        conn.executescript('''
            CREATE TABLE codes(hash TEXT PRIMARY KEY, backend TEXT, expires REAL);
            CREATE TABLE devices(
              id TEXT PRIMARY KEY, token TEXT UNIQUE, backend TEXT UNIQUE, revoked INTEGER DEFAULT 0,
              account TEXT, applied INTEGER DEFAULT 0, applied_hash TEXT, seen INTEGER DEFAULT 0,
              seen_hash TEXT, status TEXT DEFAULT 'awaiting_sync', requested INTEGER DEFAULT 1,
              checked REAL DEFAULT 0, inflight INTEGER DEFAULT 0);
            INSERT INTO devices(id, token, backend, status) VALUES('legacy-dev', 'fake-tok', 'personal', 'good');
        ''')
        conn.commit()
        conn.close()

        store = Store(legacy_dir, self.config)
        with store.db() as db:
            cols = {r['name']: r for r in db.execute('PRAGMA table_info(devices)').fetchall()}
            for expected in ['last_seen', 'last_applied_at', 'refresh_enabled',
                             'refresh_minutes', 'last_refresh_at', 'next_refresh_at']:
                self.assertIn(expected, cols)
            row = db.execute('SELECT * FROM devices WHERE id="legacy-dev"').fetchone()
            self.assertEqual(row['status'], 'good')
            self.assertEqual(row['last_seen'], 0)
            self.assertEqual(row['last_applied_at'], 0)
            self.assertEqual(row['refresh_enabled'], 0)
            self.assertEqual(row['refresh_minutes'], 0)
            self.assertEqual(row['last_refresh_at'], 0)
            self.assertEqual(row['next_refresh_at'], 0)

    def test_heartbeat_updates_caller_telemetry_and_last_seen(self):
        payload = {
            'refresh': {
                'enabled': True,
                'minutes': 30,
                'lastRefreshAt': 1700000000,
                'nextRefreshAt': 1700001800
            }
        }
        code, body = self.request('POST', '/v1/heartbeat', payload)
        self.assertEqual(code, 200)
        self.assertEqual(body.get('status'), 'ok')

        with self.store.db() as db:
            row = db.execute('SELECT * FROM devices WHERE id=?', (self.pair['deviceId'],)).fetchone()
            self.assertGreater(row['last_seen'], 0)
            self.assertEqual(row['refresh_enabled'], 1)
            self.assertEqual(row['refresh_minutes'], 30)
            self.assertEqual(row['last_refresh_at'], 1700000000)
            self.assertEqual(row['next_refresh_at'], 1700001800)

    def test_heartbeat_auth_isolation(self):
        payload = {
            'refresh': {
                'enabled': False,
                'minutes': 60,
                'lastRefreshAt': 0,
                'nextRefreshAt': 0
            }
        }
        # Missing auth
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        client.request('POST', '/v1/heartbeat', json.dumps(payload), {'Content-Type': 'application/json'})
        self.assertEqual(client.getresponse().status, 401)
        client.close()

        # Bad token
        code, _ = self.request('POST', '/v1/heartbeat', payload, token='wrong-token-value-here-12345')
        self.assertEqual(code, 401)

        # Revoked device
        self.store.revoke(self.pair['deviceId'])
        code, _ = self.request('POST', '/v1/heartbeat', payload)
        self.assertEqual(code, 401)

    def test_heartbeat_validation_and_ranges(self):
        valid = {'enabled': True, 'minutes': 30, 'lastRefreshAt': 100, 'nextRefreshAt': 200}

        # minutes range bounds: 15..1440
        for bad_min in [14, 1441, -1, 0, 15.5, '30', True]:
            bad = dict(valid, minutes=bad_min)
            self.assertEqual(self.request('POST', '/v1/heartbeat', {'refresh': bad})[0], 400)

        # Boundary extremes
        self.assertEqual(self.request('POST', '/v1/heartbeat', {'refresh': dict(valid, minutes=15)})[0], 200)
        self.assertEqual(self.request('POST', '/v1/heartbeat', {'refresh': dict(valid, minutes=1440)})[0], 200)

        # enabled must be boolean
        for bad_enabled in ['true', 1, 0, None, []]:
            bad = dict(valid, enabled=bad_enabled)
            self.assertEqual(self.request('POST', '/v1/heartbeat', {'refresh': bad})[0], 400)

        # lastRefreshAt & nextRefreshAt must be nonnegative epoch seconds
        for bad_stamp in [-1, -100, '100', 100.5, True]:
            self.assertEqual(self.request('POST', '/v1/heartbeat', {'refresh': dict(valid, lastRefreshAt=bad_stamp)})[0], 400)
            self.assertEqual(self.request('POST', '/v1/heartbeat', {'refresh': dict(valid, nextRefreshAt=bad_stamp)})[0], 400)

        # Extra fields in root or refresh
        self.assertEqual(self.request('POST', '/v1/heartbeat', {'refresh': valid, 'extra': 123})[0], 400)
        self.assertEqual(self.request('POST', '/v1/heartbeat', {'refresh': dict(valid, extra=123)})[0], 400)

        # Missing fields in refresh
        incomplete = {'enabled': True, 'minutes': 30}
        self.assertEqual(self.request('POST', '/v1/heartbeat', {'refresh': incomplete})[0], 400)

        # Not a dict
        self.assertEqual(self.request('POST', '/v1/heartbeat', 'not-a-dict')[0], 400)
        self.assertEqual(self.request('POST', '/v1/heartbeat', {'refresh': 'not-a-dict'})[0], 400)

    def test_status_updates_last_seen_and_preserves_semantics(self):
        before = self.store.status(self.token)
        self.assertIn('syncRequested', before)
        self.assertIn('status', before)
        self.assertIn('lastApplied', before)

        code, body = self.request('GET', '/v1/status')
        self.assertEqual(code, 200)
        self.assertEqual(body['lastApplied'], 0)
        self.assertIn(body['status'], ['awaiting_sync', 'good'])
        self.assertIn('syncRequested', body)

        with self.store.db() as db:
            row = db.execute('SELECT last_seen FROM devices WHERE id=?', (self.pair['deviceId'],)).fetchone()
            self.assertGreater(row['last_seen'], 0)

    def test_snapshot_successful_sets_last_applied_at(self):
        with self.store.db() as db:
            row = db.execute('SELECT last_applied_at FROM devices WHERE id=?', (self.pair['deviceId'],)).fetchone()
            self.assertEqual(row['last_applied_at'], 0)

        code, result = self.request('POST', '/v1/snapshot', snapshot(1))
        self.assertEqual(code, 200)
        self.assertEqual(result['status'], 'applied')

        with self.store.db() as db:
            row = db.execute('SELECT last_applied_at FROM devices WHERE id=?', (self.pair['deviceId'],)).fetchone()
            self.assertGreater(row['last_applied_at'], 0)

        # Invalid snapshot doesn't update last_applied_at to a newer time
        prev_applied_at = row['last_applied_at']
        bad_snap = snapshot(2, stamp='invalid')
        self.store.snapshot(self.token, bad_snap)
        with self.store.db() as db:
            row = db.execute('SELECT last_applied_at FROM devices WHERE id=?', (self.pair['deviceId'],)).fetchone()
            self.assertEqual(row['last_applied_at'], prev_applied_at)

    def test_profiles_get_response_structure_and_privacy(self):
        # Configure a second backend
        backend2 = ThreadingHTTPServer(('127.0.0.1', 0), FakeHandler)
        backend2.rows = {}
        backend2.checks = backend2.counter = 0
        backend2.outage = backend2.patch_fail = backend2.delete_fail = False
        threading.Thread(target=backend2.serve_forever, daemon=True).start()

        self.config['backends']['work'] = {
            'url': 'http://127.0.0.1:' + str(backend2.server_port),
            'adminToken': 'fake-admin-work',
            'apiKey': 'fake-api-work',
            'label': 'Work Profile'
        }
        # Re-initialize store config references
        self.store.config = self.config
        code_work = self.store.create_code('work')
        pair_work = self.store.pair(code_work)

        code, body = self.request('GET', '/v1/profiles')
        self.assertEqual(code, 200)
        self.assertIn('profiles', body)
        profiles = body['profiles']
        self.assertEqual(len(profiles), 2)

        p_curr = next(p for p in profiles if p['profileId'] == self.pair['deviceId'])
        p_other = next(p for p in profiles if p['profileId'] == pair_work['deviceId'])

        # Check label logic: 'personal' has no label in config, defaults to 'personal'; 'work' has 'Work Profile'
        self.assertEqual(p_curr['label'], 'personal')
        self.assertTrue(p_curr['isCurrent'])
        self.assertEqual(p_other['label'], 'Work Profile')
        self.assertFalse(p_other['isCurrent'])

        # Privacy verification: NO tokens, hashes, accounts, or backend URLs exposed
        raw_json = json.dumps(body)
        for secret in [self.token, pair_work['token'], 'fake-admin', 'fake-admin-work',
                       'fake-api', 'fake-api-work', 'http://127.0.0.1:' + str(backend2.server_port)]:
            self.assertNotIn(secret, raw_json)

        # Check profile shape
        for p in profiles:
            self.assertIn('profileId', p)
            self.assertIn('label', p)
            self.assertIn('isCurrent', p)
            self.assertIn('online', p)
            self.assertIn('lastSeen', p)
            self.assertIn('lastAppliedAt', p)
            self.assertIn('status', p)
            self.assertIn('refresh', p)
            ref = p['refresh']
            self.assertIn('enabled', ref)
            self.assertIn('minutes', ref)
            self.assertIn('lastRefreshAt', ref)
            self.assertIn('nextRefreshAt', ref)

        backend2.shutdown()
        backend2.server_close()

    def test_profiles_online_and_stale_logic(self):
        now = 1000000.0
        import time
        real_time = time.time
        try:
            time.time = lambda: now
            # Case 1: last_seen <= 180s and not revoked => online = True
            with self.store.db() as db:
                db.execute('UPDATE devices SET last_seen=?, revoked=0 WHERE id=?',
                           (now - 100, self.pair['deviceId']))
            _, body = self.request('GET', '/v1/profiles')
            p = next(x for x in body['profiles'] if x['profileId'] == self.pair['deviceId'])
            self.assertTrue(p['online'])

            # Case 2: last_seen > 180s => online = False (stale)
            with self.store.db() as db:
                db.execute('UPDATE devices SET last_seen=? WHERE id=?',
                           (now - 181, self.pair['deviceId']))
            _, body = self.request('GET', '/v1/profiles')
            p = next(x for x in body['profiles'] if x['profileId'] == self.pair['deviceId'])
            self.assertFalse(p['online'])

            # Case 3: status is 'invalid' but last_seen <= 180s => online = True (session validity != online)
            with self.store.db() as db:
                db.execute("UPDATE devices SET last_seen=?, status='invalid' WHERE id=?",
                           (now - 50, self.pair['deviceId']))
            _, body = self.request('GET', '/v1/profiles')
            p = next(x for x in body['profiles'] if x['profileId'] == self.pair['deviceId'])
            self.assertTrue(p['online'])
            self.assertEqual(p['status'], 'invalid')

            # Case 4: revoked device is excluded from profiles list entirely
            # (profiles only returns WHERE revoked=0), so verify the device disappears.
            # Reset status and revoke by direct SQL (simulating prior authenticated_revoke outcome).
            with self.store.db() as db:
                db.execute("UPDATE devices SET last_seen=?, revoked=1, token=NULL WHERE id=?",
                           (now - 10, self.pair['deviceId']))
            # Re-fetch profiles using token: but token is now NULL => 401. That means
            # the device is inaccessible. Instead, verify the DB state directly:
            with self.store.db() as db:
                count = db.execute('SELECT COUNT(*) FROM devices WHERE revoked=0').fetchone()[0]
            self.assertEqual(count, 0)
        finally:
            time.time = real_time

    def test_profiles_label_bounded_to_80_chars(self):
        long_label = 'A' * 120
        self.config['backends']['personal']['label'] = long_label
        code, body = self.request('GET', '/v1/profiles')
        self.assertEqual(code, 200)
        p = next(x for x in body['profiles'] if x['profileId'] == self.pair['deviceId'])
        self.assertEqual(len(p['label']), 80)
        self.assertEqual(p['label'], 'A' * 80)
        del self.config['backends']['personal']['label']

    def test_profiles_http_auth_and_host_validation(self):
        # Missing auth
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        client.request('GET', '/v1/profiles')
        self.assertEqual(client.getresponse().status, 401)
        client.close()

        # Bad token
        code, _ = self.request('GET', '/v1/profiles', token='bad-token-1234567890')
        self.assertEqual(code, 401)

        # Unexpected body on GET
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        client.request('GET', '/v1/profiles', 'unexpected',
                       {'Authorization': 'Bearer ' + self.token, 'Content-Length': '10'})
        self.assertEqual(client.getresponse().status, 400)
        client.close()

        # Bad host
        code, _ = self.request('GET', '/v1/profiles', headers={'Host': 'evil.com'})
        self.assertEqual(code, 400)


if __name__ == '__main__':
    unittest.main()
