import copy
import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from cookie_sync import Store
import test_cookie_sync as bridge_tests


class ConfigHandler(bridge_tests.FakeHandler):
    def dispatch(self):
        server = self.server
        server.calls.append((self.command, self.path))
        if self.path != '/admin/api/config':
            return super().dispatch()
        server.credentials.append((self.headers.get('Authorization'), self.headers.get('x-api-key')))
        if server.outage:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b'sensitive backend error')
            return
        if self.command == 'PUT':
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            server.puts.append(body)
            if not server.ignore_put:
                server.runtime = body
            if server.mutate_unrelated:
                server.runtime['request_timeout_sec'] = 999
        result = {'config': server.runtime, 'deploy': {'admin_token': 'sensitive'}, 'models': []}
        raw = b'not-json-sensitive' if server.bad_json else json.dumps(result).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = do_POST = do_PUT = do_DELETE = dispatch


def configure(server):
    server.RequestHandlerClass = ConfigHandler
    server.calls, server.credentials, server.puts = [], [], []
    server.ignore_put = server.mutate_unrelated = server.bad_json = False
    server.runtime = {'auto_delete_conversation': False, 'multi_turn': False,
                      'temporary_chat': False, 'request_timeout_sec': 180,
                      'api_key': 'synthetic-secret', 'future': {'nested': [1, True, 'kept']}}


class CleanupTests(unittest.TestCase):
    request = bridge_tests.BridgeTests.request
    tearDown = bridge_tests.BridgeTests.tearDown

    def setUp(self):
        bridge_tests.BridgeTests.setUp(self)
        configure(self.backend)

    def test_get_projects_only_strict_flags_and_reads_source_of_truth(self):
        expected = {'enabled': False, 'multiTurn': False, 'temporaryChat': False, 'bestEffort': True}
        self.assertEqual(self.request('GET', '/v1/cleanup'), (200, expected))
        self.backend.runtime['auto_delete_conversation'] = True
        expected['enabled'] = True
        reopened = Store(Path(self.tmp.name) / 'state', self.config)
        self.assertEqual(reopened.cleanup_settings(self.token), expected)
        self.assertEqual(self.backend.calls, [('GET', '/admin/api/config')] * 2)
        self.assertEqual(self.backend.credentials, [('Bearer fake-admin', 'fake-api')] * 2)

    def test_post_preserves_entire_runtime_config_and_verifies_reread(self):
        before = copy.deepcopy(self.backend.runtime)
        code, result = self.request('POST', '/v1/cleanup', {'enabled': True})
        self.assertEqual((code, result), (200, {'enabled': True, 'multiTurn': False,
                                              'temporaryChat': False, 'bestEffort': True}))
        before['auto_delete_conversation'] = True
        self.assertEqual(self.backend.runtime, before)
        self.assertEqual(self.backend.puts, [before])
        self.assertEqual(self.backend.calls, [('GET', '/admin/api/config'),
                                             ('PUT', '/admin/api/config'), ('GET', '/admin/api/config')])
        self.assertNotIn('synthetic-secret', json.dumps(result))
        self.assertEqual(self.backend.rows, {})

    def test_enable_conflicts_but_disable_allowed_without_changing_modes(self):
        for multi, temporary in [(True, False), (False, True), (True, True)]:
            with self.subTest(multi=multi, temporary=temporary):
                self.backend.runtime.update(multi_turn=multi, temporary_chat=temporary,
                                            auto_delete_conversation=True)
                before = copy.deepcopy(self.backend.runtime)
                self.backend.puts.clear()
                self.assertEqual(self.request('POST', '/v1/cleanup', {'enabled': True}),
                                 (409, {'error': 'cleanup_conflict'}))
                self.assertEqual(self.backend.runtime, before)
                self.assertEqual(self.backend.puts, [])
                self.assertEqual(self.request('POST', '/v1/cleanup', {'enabled': False})[0], 200)
                before['auto_delete_conversation'] = False
                self.assertEqual(self.backend.runtime, before)

    def test_unknown_fields_and_non_boolean_body_rejected_before_backend(self):
        for body in [{}, [], 'true', {'enabled': 1}, {'enabled': 0}, {'enabled': None},
                     {'enabled': 'false'}, {'enabled': True, 'multi_turn': False},
                     {'enabled': True, 'backend': 'work'}, {'enabled': False, 'url': 'http://evil'}]:
            with self.subTest(body=body):
                self.assertEqual(self.request('POST', '/v1/cleanup', body),
                                 (400, {'error': 'invalid_cleanup'}))
        self.assertEqual(self.backend.calls, [])

    def test_auth_missing_bad_and_revoked_never_contacts_backend(self):
        for method, body in [('GET', None), ('POST', {'enabled': True})]:
            client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
            client.request(method, '/v1/cleanup', None if body is None else json.dumps(body),
                           {'Content-Type': 'application/json'})
            response = client.getresponse()
            self.assertEqual(response.status, 401)
            response.read()
            client.close()
            self.assertEqual(self.request(method, '/v1/cleanup', body, token='x' * 40)[0], 401)
        with self.store.db() as db:
            db.execute('UPDATE devices SET revoked=1')
        self.assertEqual(self.request('GET', '/v1/cleanup')[0], 401)
        self.assertEqual(self.request('POST', '/v1/cleanup', {'enabled': False})[0], 401)
        self.assertEqual(self.backend.calls, [])

    def test_only_callers_dedicated_backend_is_mutated(self):
        other = ThreadingHTTPServer(('127.0.0.1', 0), ConfigHandler)
        configure(other)
        other.outage = False
        threading.Thread(target=other.serve_forever, daemon=True).start()
        try:
            self.config['backends']['work'] = {'url': 'http://127.0.0.1:' + str(other.server_port),
                                                'adminToken': 'work-admin', 'apiKey': 'work-api'}
            work = self.store.pair(self.store.create_code('work'))
            self.assertEqual(self.request('POST', '/v1/cleanup', {'enabled': True}, token=work['token'])[0], 200)
            self.assertTrue(other.runtime['auto_delete_conversation'])
            self.assertFalse(self.backend.runtime['auto_delete_conversation'])
            self.assertEqual(self.backend.calls, [])
            self.assertEqual(other.credentials, [('Bearer work-admin', 'work-api')] * 3)
            self.assertFalse(self.request('GET', '/v1/cleanup')[1]['enabled'])
        finally:
            other.shutdown()
            other.server_close()

    def test_backend_flags_missing_or_wrong_type_fail_closed_without_put(self):
        original = copy.deepcopy(self.backend.runtime)
        for key in ['auto_delete_conversation', 'multi_turn', 'temporary_chat']:
            for value in [None, 0, 1, 'false', [], {}]:
                with self.subTest(key=key, value=value):
                    self.backend.runtime = dict(original, **{key: value})
                    self.assertEqual(self.request('GET', '/v1/cleanup'), (503, {'error': 'backend_unavailable'}))
                    self.assertEqual(self.request('POST', '/v1/cleanup', {'enabled': True})[0], 503)
            self.backend.runtime = dict(original)
            del self.backend.runtime[key]
            self.assertEqual(self.request('POST', '/v1/cleanup', {'enabled': False})[0], 503)
        for value in [None, [], True, 'sensitive']:
            self.backend.runtime = value
            self.assertEqual(self.request('GET', '/v1/cleanup')[0], 503)
        self.assertEqual(self.backend.puts, [])

    def test_ignored_put_and_unrelated_changes_fail_verification(self):
        for flag in ['ignore_put', 'mutate_unrelated']:
            with self.subTest(flag=flag):
                configure(self.backend)
                setattr(self.backend, flag, True)
                self.assertEqual(self.request('POST', '/v1/cleanup', {'enabled': True}),
                                 (503, {'error': 'backend_partial_failure'}))
                self.assertEqual(self.backend.calls[-1], ('GET', '/admin/api/config'))

    def test_backend_errors_are_sanitized(self):
        for flag in ['outage', 'bad_json']:
            with self.subTest(flag=flag):
                setattr(self.backend, flag, True)
                self.assertEqual(self.request('GET', '/v1/cleanup'), (503, {'error': 'backend_unavailable'}))
                self.assertEqual(self.request('POST', '/v1/cleanup', {'enabled': False}),
                                 (503, {'error': 'backend_unavailable'}))
                setattr(self.backend, flag, False)

    def test_allowlist_does_not_expose_config_or_arbitrary_paths(self):
        for method, path in [('GET', '/admin/api/config'), ('POST', '/v1/cleanup?backend=work'),
                             ('DELETE', '/v1/cleanup'), ('GET', '/v1/cleanup/extra')]:
            self.assertEqual(self.request(method, path, {'enabled': True} if method == 'POST' else None)[0], 404)
        self.assertEqual(self.request('GET', '/v1/cleanup', {'enabled': True})[0], 400)
        self.assertEqual(self.backend.calls, [])


if __name__ == '__main__':
    unittest.main()
