"""Real unpacked-extension E2E against the dedicated remote e2e-test target.

Prerequisites: build/chrome-sync, running remote-fixture.py, and a freshly issued
private e2e.connection.json. Never use a production pairing. Does not mock fetch,
permissions, or the receiver. Only the Gemini document and refresh alarm timing
are test overrides. Required-host test manifest bypasses Chrome's OS prompt;
interactive optional-host permission approval is NOT covered by this test.
"""
import argparse
import json
from pathlib import Path
import shutil
import tempfile
import time
from urllib.request import Request, build_opener, ProxyHandler

from playwright.sync_api import sync_playwright

ENDPOINT = 'http://100.100.100.100:8787'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connection', type=Path,
                        default=Path.home() / '.gemini-cookie-sync' / 'e2e.connection.json')
    parser.add_argument('--screenshot', type=Path,
                        default=Path.home() / '.gemini-cookie-sync' / 'e2e-settings.png')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    connection = json.loads(args.connection.read_text(encoding='utf-8-sig'))
    assert connection.get('endpoint', '').rstrip('/') == ENDPOINT, 'Unexpected E2E endpoint'
    assert connection.get('target') == 'e2e-test', 'Private connection must identify e2e-test target'
    assert connection.get('token') and connection.get('deviceId'), 'Missing E2E pairing'
    assert not args.screenshot.resolve().is_relative_to(root), 'Evidence must remain outside repository'
    opener = build_opener(ProxyHandler({}))

    def remote(path):
        request = Request(ENDPOINT + path, headers={
            'Authorization': 'Bearer ' + connection['token']})
        with opener.open(request, timeout=10) as response:
            return json.load(response)

    def cookie(name, suffix):
        return {'name': name, 'value': 'e2e-synthetic-' + suffix,
                'domain': '.google.com', 'path': '/', 'secure': True,
                'httpOnly': True, 'sameSite': 'None', 'expires': time.time() + 3600}

    with tempfile.TemporaryDirectory(prefix='gemini-remote-e2e-') as temporary:
        temporary = Path(temporary)
        extension = temporary / 'extension'
        shutil.copytree(root / 'build' / 'chrome-sync', extension)
        (extension / 'sync' / 'default-connection.json').write_text(
            json.dumps(connection), encoding='utf-8')
        manifest_path = extension / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['host_permissions'] = list(dict.fromkeys(manifest.get('host_permissions', []) + [
            'http://100.100.100.100/*', 'https://your-node.your-tailnet.ts.net/*', 'https://google.com/*', 'https://gemini.google.com/*']))
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(temporary / 'profile'), channel='chromium', headless=True,
                viewport={'width': 1100, 'height': 820},
                args=['--disable-extensions-except=' + str(extension),
                      '--load-extension=' + str(extension)])
            try:
                worker = (context.service_workers[0] if context.service_workers else
                          context.wait_for_event('serviceworker'))
                ident = worker.url.split('/')[2]
                context.add_cookies([cookie('__Secure-1PSID', 'account'),
                                     cookie('__Secure-1PSIDTS', 'ts-initial')])
                loads = []

                def gemini_document(route):
                    loads.append(time.monotonic())
                    route.fulfill(status=200, content_type='text/html',
                                  body='<!doctype html><title>Synthetic E2E Gemini tab</title><p>Test only</p>')

                context.route('https://gemini.google.com/**', gemini_document)
                gemini = context.new_page()
                gemini.goto('https://gemini.google.com/app')
                page = context.new_page()
                page.goto('chrome-extension://' + ident + '/sync/settings.html')
                page.bring_to_front()
                page.locator('#one-click-connect').click()
                deadline = time.monotonic() + 20
                while not page.locator('#unpair').is_enabled() and time.monotonic() < deadline:
                    page.wait_for_timeout(200)
                assert page.locator('#unpair').is_enabled(), 'One-click activation failed'

                def state():
                    return worker.evaluate("async () => (await chrome.storage.local.get('geminiSync')).geminiSync || {}")

                def wait_state(predicate, timeout, failure):
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        value = state()
                        if predicate(value):
                            return value
                        page.wait_for_timeout(200)
                    raise AssertionError(failure)

                first = wait_state(lambda s: s.get('status') == 'applied', 95,
                                   'Initial real receiver snapshot was not applied')
                assert remote('/v1/status')['lastApplied'] >= 1
                context.add_cookies([cookie('__Secure-1PSIDTS', 'ts-rotated')])
                rotated = wait_state(lambda s: s.get('status') == 'applied' and
                                     s.get('sequence', 0) > first['sequence'], 20,
                                     'Cookie change did not auto-sync within 20 seconds')
                assert rotated['lastApplied'] >= first['lastApplied']
                profiles = page.evaluate("chrome.runtime.sendMessage({type:'gemini-sync', action:'fetchProfiles'})")
                assert profiles.get('ok') is not False
                assert any(p.get('isCurrent') and p.get('online') for p in profiles['profiles']), 'Current profile not online'
                page.reload()
                page.locator('#refresh-enabled').check()
                page.locator('#minutes').fill('15')
                tabs = worker.evaluate("chrome.tabs.query({url:'https://gemini.google.com/*'})")
                assert len(tabs) == 1 and not tabs[0]['active'], 'Gemini fixture tab must be inactive'
                page.locator('#tab').select_option(str(tabs[0]['id']))
                page.locator('#refresh button[type=submit], #refresh button:not([type])').click()
                wait_state(lambda s: s.get('refreshEnabled') and s.get('refreshMinutes') == 15,
                           10, '15-minute refresh settings not saved')
                alarms = worker.evaluate('chrome.alarms.getAll()')
                refresh_alarm = next(a for a in alarms if a['name'] == 'gemini-sync-refresh')
                assert refresh_alarm['periodInMinutes'] == 15
                before = len(loads)
                # Timing override only; the real alarm listener calls real tabs.reload.
                worker.evaluate("chrome.alarms.create('gemini-sync-refresh', {delayInMinutes:0.01})")
                deadline = time.monotonic() + 20
                while len(loads) <= before and time.monotonic() < deadline:
                    page.wait_for_timeout(200)
                assert len(loads) > before, 'Refresh alarm did not reload selected inactive tab'
                wait_state(lambda s: s.get('lastRefreshAt', 0) > 0, 5, 'Refresh metadata missing')
                page.reload()
                page.locator('#refresh-status-btn').click()
                assert connection['token'] not in page.locator('body').inner_text(), 'Token visible in settings'
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                page.evaluate('window.scrollTo(0, 0)')
                page.screenshot(path=str(args.screenshot), full_page=False)
                context.clear_cookies()
                deadline = time.monotonic() + 20
                logged_out = False
                while time.monotonic() < deadline:
                    if remote('/v1/status').get('status') == 'logged_out':
                        logged_out = True
                        break
                    page.wait_for_timeout(250)
                assert logged_out, 'Receiver did not confirm logout (backend disable path)'
                print(json.dumps({'realReceiver': True, 'oneClick': True, 'autoSyncWithin20s': True,
                                  'currentProfileOnline': True, 'savedRefreshMinutes': 15,
                                  'realInactiveTabReload': True, 'logoutConfirmed': True,
                                  'permissionPromptCovered': False,
                                  'testOverrides': ['required host manifest', 'Gemini document', 'refresh alarm delay']}))
            finally:
                context.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Playwright/HTTP exception details can contain private URLs or call payloads.
        import traceback
        print(json.dumps({'ok': False, 'errorType': type(error).__name__, 'detail': str(error).split('\n')[0][:180], 'frames': [{'line': f.lineno, 'function': f.name} for f in traceback.extract_tb(error.__traceback__)]}))
        raise SystemExit(1) from None
