"""Render real extension settings with explicitly synthetic status data for documentation."""
import json
from pathlib import Path
import tempfile
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
images = root / 'docs/images'
images.mkdir(parents=True, exist_ok=True)
extension = root / 'build/chrome'
if not (extension / 'manifest.json').exists():
    raise SystemExit('Run npx grunt copy:chrome replace:chrome first')
with tempfile.TemporaryDirectory(prefix='docs-screenshot-') as profile:
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(profile, channel='chromium', headless=True,
            viewport={'width': 1440, 'height': 1100},
            args=['--disable-extensions-except=' + str(extension), '--load-extension=' + str(extension)])
        try:
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            ident = worker.url.split('/')[2]
            page = context.new_page()
            page.add_init_script('''(() => {
                const now = Math.floor(Date.now()/1000);
                chrome.runtime.sendMessage = async message => {
                    if(message.action === 'state') return {paired:true,endpoint:'https://your-node.your-tailnet.ts.net:8443',
                        status:'applied',lastApplied:now*1000-30000,refreshEnabled:true,refreshMinutes:30,
                        refreshTabId:7,tabs:[{id:7,active:false}],lastRefreshAt:now-120,nextRefreshAt:now+1680};
                    if(message.action === 'fetchProfiles') return {profiles:[
                        {label:'Personal profile (demo)',isCurrent:true,online:true,status:'good',lastSeen:now-12,lastAppliedAt:now-30,refresh:{enabled:true,minutes:30,nextRefreshAt:now+1680}},
                        {label:'Work profile (demo)',isCurrent:false,online:true,status:'good',lastSeen:now-40,lastAppliedAt:now-300,refresh:{enabled:true,minutes:60,nextRefreshAt:now+2400}},
                        {label:'Laptop profile (demo)',isCurrent:false,online:false,status:'invalid',lastSeen:now-7200,lastAppliedAt:now-8000,refresh:{enabled:false,minutes:30,nextRefreshAt:0}}
                    ]};
                    return {ok:true};
                };
            })()''')
            page.goto(f'chrome-extension://{ident}/sync/settings.html')
            page.wait_for_load_state('networkidle')
            page.locator('#profiles-tbody tr').first.wait_for()
            page.evaluate('''() => {
                const note=document.createElement('p');note.textContent='DOCUMENTATION DEMO — synthetic profile data, no real account or credentials';
                note.style.cssText='padding:12px;background:#fff3cd;color:#513c00;border:1px solid #e6c86b;font-weight:bold';
                document.querySelector('main').prepend(note);
            }''')
            page.screenshot(path=str(images/'settings-demo.png'), full_page=True)
            page.locator('#profiles-section').screenshot(path=str(images/'profiles-demo.png'))
            print(json.dumps({'screenshots':['docs/images/settings-demo.png','docs/images/profiles-demo.png'], 'data':'synthetic only'}))
        finally:
            context.close()
