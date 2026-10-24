/* eslint-disable require-jsdoc */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { setImmediate } from 'node:timers/promises';
import vm from 'node:vm';

async function fixture({ flags, paired = true } = {}) {
  const html = await readFile(
    new URL('../sync/settings.html', import.meta.url),
    'utf8'
  );
  const source = await readFile(
    new URL('../sync/settings.js', import.meta.url),
    'utf8'
  );
  const elements = new Map();
  const make = () => ({
    style: {},
    listeners: {},
    checked: false,
    disabled: false,
    value: '',
    textContent: '',
    innerHTML: '',
    addEventListener(name, callback) {
      this.listeners[name] = callback;
    },
    replaceChildren() {},
    append() {},
  });
  for (const match of html.matchAll(/id="([^"]+)"/g))
    elements.set(match[1], make());
  const calls = [];
  let poll;
  const state = {
    paired,
    status: 'paired',
    tabs: [],
    endpoint: '',
    refreshEnabled: false,
  };
  const context = {
    document: { getElementById: id => elements.get(id), createElement: make },
    window: { addEventListener() {} },
    chrome: {
      runtime: {
        getURL: path => path,
        async sendMessage(message) {
          calls.push(message);
          if (message.action === 'state') return state;
          if (message.action === 'fetchProfiles') return { profiles: [] };
          if (message.action === 'getCleanup')
            return flags || { ok: false, error: 'Unavailable' };
          return { ok: true };
        },
      },
      extension: { inIncognitoContext: false },
    },
    fetch: async () => ({ ok: false }),
    setInterval(callback) {
      poll = callback;
      return 1;
    },
    clearInterval() {},
  };
  vm.runInNewContext(source.replace(/^import .*;\r?\n/m, ''), context);
  for (let i = 0; i < 5; i++) await setImmediate();
  return { elements, calls, poll, state };
}

test('cleanup UI loads actual flags, saves explicitly, rereads and preserves draft on profile polls', async () => {
  const f = await fixture({
    flags: {
      enabled: true,
      multiTurn: false,
      temporaryChat: false,
      bestEffort: true,
    },
  });
  const checkbox = f.elements.get('cleanup-enabled');
  assert.ok(checkbox, 'cleanup checkbox exists');
  assert.equal(checkbox.checked, true);
  assert.equal(f.calls.filter(c => c.action === 'setCleanup').length, 0);
  checkbox.checked = false;
  await f.poll();
  assert.equal(checkbox.checked, false);
  assert.equal(f.calls.filter(c => c.action === 'getCleanup').length, 1);
  await f.elements.get('cleanup').listeners.submit({ preventDefault() {} });
  assert.equal(f.calls.find(c => c.action === 'setCleanup').enabled, false);
  assert.equal(f.calls.filter(c => c.action === 'getCleanup').length, 2);
});

test('unknown cleanup state is off with visible error and cannot be enabled', async () => {
  const f = await fixture();
  const checkbox = f.elements.get('cleanup-enabled');
  assert.ok(checkbox, 'cleanup checkbox exists');
  assert.equal(checkbox.checked, false);
  assert.equal(checkbox.disabled, true);
  assert.match(f.elements.get('cleanup-status').textContent, /unavailable/i);
});

test('incompatible backend blocks enable but permits turning off existing cleanup', async () => {
  for (const mode of ['multiTurn', 'temporaryChat']) {
    for (const enabled of [false, true]) {
      const f = await fixture({
        flags: {
          enabled,
          multiTurn: false,
          temporaryChat: false,
          bestEffort: true,
          [mode]: true,
        },
      });
      const checkbox = f.elements.get('cleanup-enabled');
      assert.ok(checkbox, 'cleanup checkbox exists');
      assert.equal(checkbox.disabled, !enabled);
      if (enabled) {
        checkbox.checked = false;
        await f.elements
          .get('cleanup')
          .listeners.submit({ preventDefault() {} });
        assert.equal(
          f.calls.find(c => c.action === 'setCleanup').enabled,
          false
        );
      }
    }
  }
});

test('unpaired cleanup UI never reads or mutates backend cleanup', async () => {
  const f = await fixture({ paired: false });
  assert.ok(f.elements.get('cleanup-enabled'), 'cleanup checkbox exists');
  assert.equal(f.elements.get('cleanup-enabled').disabled, true);
  assert.equal(
    f.calls.some(c => /Cleanup/.test(c.action)),
    false
  );
});
