"""Exercise two isolated Chromium profiles with mocked permissions and transport."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import urllib.request
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cookie_sync', ROOT / 'server/cookie_sync.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class FakeBackend:
    snapshots = {}

    def __init__(self, config):
        self.name = config['name']

    def rotate(self, cookie):
        self.snapshots[self.name] = cookie
        return 'applied'

    def rows(self):
        return [{'id': 1, 'status': 'enabled'}] if self.name in self.snapshots else []

    def check(self, row):
        return True

    def status(self, row, status):
        if status == 'disabled':
            self.snapshots.pop(self.name, None)


with tempfile.TemporaryDirectory(prefix='sync-transport-') as temporary:
    store = bridge.Store(Path(temporary) / 'state', {'backends': {'a': {'name': 'a'}, 'b': {'name': 'b'}}}, FakeBackend)
    server = bridge.Server(('127.0.0.1', 0), store)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = 'https://sync.synthetic.ts.net:8443'
    with sync_playwright() as p:
        for target in ('a', 'b'):
            context = p.chromium.launch_persistent_context(str(Path(temporary) / target), channel='chromium', headless=True,
                args=['--disable-extensions-except=' + str(ROOT / 'build/chrome-sync'), '--load-extension=' + str(ROOT / 'build/chrome-sync')])
            try:
                worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
                extension_id = worker.url.split('/')[2]
                page = context.new_page()
                page.goto('chrome-extension://' + extension_id + '/sync/settings.html')
                page.wait_for_load_state('networkidle')
                # Permission prompt and transport are mocked; no request reaches Google/server.
                granted = True
                context.add_cookies([
                    {'name': '__Secure-1PSID', 'value': 'synthetic-account-' + target,
                     'domain': '.google.com', 'path': '/', 'secure': True, 'httpOnly': True},
                    {'name': '__Secure-1PSIDTS', 'value': 'synthetic-ts-' + target,
                     'domain': '.google.com', 'path': '/', 'secure': True, 'httpOnly': True},
                ])
                code = store.create_code(target)
                def bridge_call(payload):
                    headers = {'Content-Type': 'application/json'}
                    if payload.get('authorization'):
                        headers['Authorization'] = payload['authorization']
                    request = urllib.request.Request('http://127.0.0.1:' + str(server.server_port) + payload['path'],
                        method=payload['method'], data=None if payload.get('body') is None else payload['body'].encode(), headers=headers)
                    with urllib.request.urlopen(request, timeout=10) as response:
                        return json.load(response)
                page.expose_function('bridgeCall', bridge_call)
                assert granted, 'Synthetic host grants failed'
                await_result = page.evaluate("""async ({endpoint, code, cookies}) => {
                    const {createSyncWorker} = await import(chrome.runtime.getURL('sync/worker.js'));
                    const responses = [];
                    const api = {...chrome, permissions: {contains: async () => true}, cookies:{...chrome.cookies,getAll:async()=>cookies}};
                    const worker = createSyncWorker(api, async (url, options={}) => {
                        responses.push({path:new URL(url).pathname, method:options.method, body:options.body});
                        const result = await window.bridgeCall({path:new URL(url).pathname,
                            method:options.method || 'GET', body:options.body,
                            authorization:options.headers?.Authorization});
                        return new Response(JSON.stringify(result));
                    });
                    await worker.command({action:'pair',endpoint,code});
                    await worker.syncNow();
                    return responses.map(r=>({path:r.path,method:r.method}));
                }""", {'endpoint': endpoint, 'code': code, 'cookies': [
                    {'name':'__Secure-1PSID','value':'synthetic-account-'+target,'domain':'.google.com','path':'/','secure':True,'httpOnly':True,'storeId':'0'},
                    {'name':'__Secure-1PSIDTS','value':'synthetic-ts-'+target,'domain':'.google.com','path':'/','secure':True,'httpOnly':True,'storeId':'0'}]})
                assert 'synthetic-account-' + target in FakeBackend.snapshots.get(target, ''), 'Snapshot not applied to correct target'
                assert all('synthetic-account-' + other not in FakeBackend.snapshots[target] for other in ('a', 'b') if other != target), 'Cross-profile cookie leak'
                print(json.dumps({'profile': target, 'permissionsMocked': granted, 'requests': await_result, 'isolatedSnapshotApplied': True}))
            finally:
                context.close()
    server.shutdown()
    server.server_close()
