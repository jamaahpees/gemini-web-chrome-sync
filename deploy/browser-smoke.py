"""Load the unpacked extension in an isolated Playwright Chromium profile."""
import json
from pathlib import Path
import tempfile
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
extension = root / 'build' / 'chrome-sync'
if not (extension / 'manifest.json').exists():
    raise SystemExit('Build unpacked Chrome package first')
with tempfile.TemporaryDirectory(prefix='gemini-sync-smoke-') as profile:
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            profile, channel='chromium', headless=True,
            args=['--disable-extensions-except=' + str(extension), '--load-extension=' + str(extension)],
        )
        try:
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            extension_id = worker.url.split('/')[2]
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto('chrome-extension://' + extension_id + '/sync/settings.html')
            page.wait_for_load_state('networkidle')
            assert page.locator('input').count() >= 2, 'Pairing fields missing'
            assert page.locator('button').count() >= 2, 'Controls missing'
            assert not errors, 'Settings script errors: ' + repr(errors)
            alarms = worker.evaluate('chrome.alarms.getAll()')
            storage = worker.evaluate('chrome.storage.local.get(null)')
            assert not any('cookie' in k.lower() for k in storage), 'Cookie payload persisted'
            print(json.dumps({'extensionLoaded': True, 'settingsRendered': True, 'pageErrors': errors,
                              'alarmNames': [a['name'] for a in alarms], 'storageKeys': list(storage)}))
        finally:
            context.close()
