import assert from 'node:assert/strict';
import fs from 'node:fs';
import { test } from 'node:test';

test('Chrome package exposes settings shortcut and original sync icons', () => {
  const manifest = JSON.parse(fs.readFileSync('manifest.chrome.json', 'utf8'));
  assert.equal(manifest.action.default_title, 'Gemini Web Sync');
  for (const path of Object.values(manifest.icons)) {
    assert.match(path, /gemini-sync-/);
    assert.ok(fs.existsSync(path));
  }
  const popup = fs.readFileSync('interface/popup/cookie-list.html', 'utf8');
  assert.match(popup, /id="open-gemini-sync"/);
  assert.match(popup, /sync-shortcut\.js/);
});
