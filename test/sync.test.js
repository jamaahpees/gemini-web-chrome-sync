/* eslint-disable require-jsdoc */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { setImmediate } from 'node:timers';

import {
  allowedCookie,
  cookieIdentity,
  endpointOrigin,
  isLogout,
  permissionPattern,
  refreshMinutes,
  retryMinutes,
  snapshotCookies,
} from '../sync/helpers.js';
import { createSyncWorker } from '../sync/worker.js';

const cookie = (overrides = {}) => ({
  name: '__Secure-1PSID',
  value: 'secret',
  domain: '.google.com',
  path: '/',
  storeId: '0',
  secure: true,
  httpOnly: true,
  ...overrides,
});

test('endpoint rejects alternate origins, credentials and URL decorations', () => {
  assert.equal(
    endpointOrigin('https://host.tailnet.ts.net/'),
    'https://host.tailnet.ts.net'
  );
  for (const input of [
    'http://host.ts.net',
    'https://ts.net',
    'https://host.ts.net.evil.com',
    'https://user@host.ts.net',
    'https://host.ts.net/path',
    'https://host.ts.net?x',
    'https://host.ts.net#',
    'https://host.ts.net:443',
    'https://host.ts.net/a/..',
    'https://host.ts.net\\@evil.com',
  ]) {
    assert.throws(() => endpointOrigin(input), input);
  }
});

test('private Serve port is allowed but other custom ports are rejected', () => {
  assert.equal(
    endpointOrigin('https://your-node.your-tailnet.ts.net:8443'),
    'https://your-node.your-tailnet.ts.net:8443'
  );
  assert.throws(() => endpointOrigin('https://host.ts.net:9443'));
});

test('mixed cookies preserve standard security metadata and require secure prefixes at root', () => {
  const cookies = snapshotCookies([
    cookie({ name: 'COMPASS' }),
    cookie({ name: '__Secure-BUCKET' }),
    cookie({ name: 'SID', secure: false }),
    cookie({ name: 'HSID', secure: false }),
    cookie({ name: 'APISID', secure: false }),
    cookie({ path: '/app' }),
    cookie({ name: '__Secure-3PSID', secure: false }),
  ]);
  assert.deepEqual(cookies.map(c => c.name).sort(), [
    'APISID',
    'COMPASS',
    'HSID',
    'SID',
    '__Secure-BUCKET',
  ]);
  assert.equal(cookies.find(c => c.name === 'SID').secure, false);
});

test('filter excludes other domains, names, partitions and stores', () => {
  assert.equal(allowedCookie(cookie()), true);
  for (const overrides of [
    { domain: 'accounts.google.com' },
    { name: 'random' },
    { storeId: '1' },
    { partitionKey: { topLevelSite: 'https://google.com' } },
    { incognito: true },
  ]) {
    assert.equal(allowedCookie(cookie(overrides)), false);
  }
});

test('snapshot preserves full identity, deterministic order and only wire fields', () => {
  const cookies = [
    cookie(),
    cookie({ path: '/app', expirationDate: 100 }),
    cookie({ domain: 'gemini.google.com' }),
  ];
  assert.notEqual(cookieIdentity(cookies[0]), cookieIdentity(cookies[1]));
  assert.deepEqual(
    snapshotCookies(cookies),
    snapshotCookies([...cookies].reverse())
  );
  assert.equal(snapshotCookies(cookies).length, 2);
  assert.equal('storeId' in snapshotCookies(cookies)[0], false);
  assert.equal(isLogout(snapshotCookies(cookies)), false);
  assert.equal(isLogout([]), true);
  assert.equal(isLogout([cookie({ value: '' })]), true);
});

test('Chrome permission pattern is exact-host without a port', () => {
  assert.equal(
    permissionPattern('https://host.tailnet.ts.net:8443'),
    'https://host.tailnet.ts.net/*'
  );
  assert.equal(
    permissionPattern('https://host.tailnet.ts.net'),
    'https://host.tailnet.ts.net/*'
  );
});

test('direct Tailscale transport accepts only designated private API', () => {
  assert.equal(
    endpointOrigin('http://100.100.100.100:8787'),
    'http://100.100.100.100:8787'
  );
  assert.equal(
    permissionPattern('http://100.100.100.100:8787'),
    'http://100.100.100.100/*'
  );
  for (const endpoint of [
    'http://100.100.100.100:8083',
    'http://192.168.1.1:8787',
    'http://100.112.195.85:8787',
    'http://100.100.100.100:8787/path',
  ]) {
    assert.throws(() => endpointOrigin(endpoint));
  }
});

test('retry and refresh bounds', () => {
  assert.deepEqual([0, 1, 2, 3, 100].map(retryMinutes), [1, 2, 4, 5, 5]);
  assert.equal(refreshMinutes(30), 30);
  for (const value of [0, 14, 15.5, 1441, NaN])
    assert.throws(() => refreshMinutes(value));
});

function fixture(fetcher) {
  const data = {
    geminiSync: {
      endpoint: 'https://host.tailnet.ts.net',
      token: 'test-token',
      sequence: 4,
      status: 'paired',
    },
  };
  const listeners = {};
  const event = name => ({
    addListener: callback => {
      listeners[name] = callback;
    },
  });
  const alarms = [];
  let cookies = [cookie()];
  const api = {
    storage: {
      local: {
        setAccessLevel: async options => {
          assert.equal(options.accessLevel, 'TRUSTED_CONTEXTS');
        },
        get: async () => structuredClone(data),
        set: async values => Object.assign(data, structuredClone(values)),
        remove: async key => {
          delete data[key];
        },
      },
    },
    permissions: { contains: async () => true },
    cookies: {
      getAll: async options => {
        assert.equal(options.storeId, '0');
        return cookies;
      },
      onChanged: event('changed'),
    },
    alarms: {
      get: async name => alarms.find(alarm => alarm.name === name),
      create: async (name, options) => alarms.push({ name, ...options }),
      clear: async () => true,
      onAlarm: event('alarm'),
    },
    runtime: {
      id: 'extension',
      getURL: path => 'chrome-extension://extension/' + path,
      onStartup: event('startup'),
      onInstalled: event('installed'),
      onMessage: event('message'),
    },
    contextMenus: { create: () => {}, onClicked: event('menu') },
    tabs: { query: async () => [], get: async () => null },
  };
  const worker = createSyncWorker(api, (...args) => fetcher(data, ...args));
  return {
    worker,
    data,
    alarms,
    listeners,
    setCookies: value => {
      cookies = value;
    },
  };
}
const response = data => ({ ok: true, status: 200, json: async () => data });

test('connection import verifies token before storing and refuses replacement', async () => {
  const f = fixture(async () =>
    response({ syncRequested: true, status: 'awaiting_sync', lastApplied: 0 })
  );
  delete f.data.geminiSync;
  const connection = {
    endpoint: 'https://host.tailnet.ts.net:8443',
    deviceId: 'device-test',
    token: 'a'.repeat(43),
  };
  assert.deepEqual(
    await f.worker.command({ action: 'importConnection', connection }),
    { ok: true }
  );
  assert.equal(f.data.geminiSync.token, connection.token);
  assert.equal(f.data.geminiSync.needsSync, true);
  await assert.rejects(
    f.worker.command({ action: 'importConnection', connection })
  );
});

test('failed connection import never saves credentials', async () => {
  const f = fixture(async () => ({ ok: false, status: 401 }));
  delete f.data.geminiSync;
  await assert.rejects(
    f.worker.command({
      action: 'importConnection',
      connection: {
        endpoint: 'https://host.tailnet.ts.net:8443',
        deviceId: 'device-test',
        token: 'b'.repeat(43),
      },
    })
  );
  assert.equal(f.data.geminiSync, undefined);
});

test('sequence is persisted before dispatch and snapshots are never stored', async () => {
  const f = fixture(async (data, url, options) => {
    const body = JSON.parse(options.body);
    assert.equal(data.geminiSync.sequence, body.sequence);
    assert.equal(body.sequence, 5);
    assert.equal(url, 'https://host.tailnet.ts.net/v1/snapshot');
    assert.equal(options.redirect, 'error');
    assert.equal(JSON.stringify(data).includes('secret'), false);
    return response({ status: 'applied', sequence: body.sequence });
  });
  await f.worker.ready();
  await f.worker.syncNow();
  assert.equal(f.data.geminiSync.status, 'applied');
  assert.equal(f.data.geminiSync.needsSync, false);
});

test('retry reads fresh cookies and advances durable sequence', async () => {
  let count = 0;
  const f = fixture(async (data, url, options) => {
    const body = JSON.parse(options.body);
    if (++count === 1) throw new Error('network');
    assert.equal(body.cookies[0].value, 'fresh');
    assert.equal(body.sequence, 6);
    return response({ status: 'unchanged', sequence: body.sequence });
  });
  await f.worker.ready();
  await f.worker.syncNow();
  assert.equal(f.data.geminiSync.status, 'retrying');
  assert.ok(
    f.alarms.some(a => a.name.endsWith('retry') && a.delayInMinutes === 1)
  );
  f.setCookies([cookie({ value: 'fresh' })]);
  await f.worker.syncNow();
  assert.equal(f.data.geminiSync.status, 'unchanged');
});

test('unchanged retry reuses the durable pending sequence', async () => {
  let calls = 0;
  const sequences = [];
  const f = fixture(async (data, url, options) => {
    const body = JSON.parse(options.body);
    sequences.push(body.sequence);
    if (++calls === 1) throw new Error('network');
    return response({ status: 'unchanged', sequence: body.sequence });
  });
  await f.worker.ready();
  await f.worker.syncNow();
  await f.worker.syncNow();
  assert.deepEqual(sequences, [5, 5]);
});

test('account change blocks subsequent automatic snapshots', async () => {
  let calls = 0;
  const f = fixture(async (data, url, options) => {
    calls++;
    return response({
      status: 'account_changed',
      sequence: JSON.parse(options.body).sequence,
    });
  });
  await f.worker.ready();
  await f.worker.syncNow();
  await f.worker.syncNow();
  assert.equal(calls, 1);
  assert.equal(f.data.geminiSync.status, 'account_changed');
});

test('singleflight reruns with a fresh snapshot when marked dirty during dispatch', async () => {
  let release;
  let calls = 0;
  const f = fixture(async (data, url, options) => {
    calls++;
    if (calls === 1)
      await new Promise(resolve => {
        release = resolve;
      });
    return response({
      status: 'applied',
      sequence: JSON.parse(options.body).sequence,
    });
  });
  await f.worker.ready();
  const first = f.worker.syncNow();
  while (!release) await new Promise(resolve => setImmediate(resolve));
  f.setCookies([cookie({ value: 'changed-during-send' })]);
  f.worker.syncNow();
  release();
  await first;
  assert.equal(calls, 2);
  assert.equal(f.data.geminiSync.sequence, 6);
});

test('acknowledged and invalid hashes are skipped, new invalid replacement recovers', async () => {
  let calls = 0;
  const f = fixture(async (data, url, options) => {
    calls++;
    return response({
      status: calls === 1 ? 'invalid' : 'applied',
      sequence: JSON.parse(options.body).sequence,
    });
  });
  await f.worker.ready();
  await f.worker.syncNow();
  await f.worker.syncNow();
  assert.equal(calls, 1);
  f.setCookies([cookie({ value: 'replacement' })]);
  await f.worker.syncNow();
  assert.equal(calls, 2);
  await f.worker.syncNow();
  assert.equal(calls, 2);
});

test('logout sends canonical empty cookies even when other cookies remain', async () => {
  const f = fixture(async (data, url, options) => {
    const body = JSON.parse(options.body);
    assert.equal(body.logout, true);
    assert.deepEqual(body.cookies, []);
    return response({ status: 'applied', sequence: body.sequence });
  });
  await f.worker.ready();
  f.setCookies([cookie({ name: 'SAPISID' })]);
  await f.worker.syncNow();
  assert.equal(f.data.geminiSync.status, 'applied');
});

test('pair accepts URL-safe codes and revoke failure retains credentials', async () => {
  const f = fixture(async () => {
    throw new Error('offline');
  });
  await f.worker.ready();
  delete f.data.geminiSync;
  await assert.rejects(
    f.worker.command({
      action: 'pair',
      endpoint: 'https://host.ts.net:8443',
      code: 'abc_def-123',
    }),
    error => error.message !== 'Enter a valid pairing code.'
  );
  f.data.geminiSync = {
    endpoint: 'https://host.ts.net:8443',
    token: 'retain',
    status: 'paired',
  };
  await assert.rejects(f.worker.command({ action: 'unpair' }));
  assert.equal(f.data.geminiSync.token, 'retain');
});

test('account_changed permits canonical logout but blocks nonempty snapshots', async () => {
  let calls = 0;
  const f = fixture(async (data, url, options) => {
    calls++;
    const body = JSON.parse(options.body);
    assert.equal(body.logout, true);
    assert.deepEqual(body.cookies, []);
    return response({ status: 'applied', sequence: body.sequence });
  });
  await f.worker.ready();
  f.data.geminiSync.status = 'account_changed';
  await f.worker.syncNow();
  assert.equal(calls, 0);
  f.setCookies([]);
  await f.worker.syncNow();
  assert.equal(calls, 1);
  f.setCookies([cookie({ value: 'different-account' })]);
  await f.worker.syncNow();
  assert.equal(calls, 1);
});

test('messages from non-settings contexts are rejected without disclosing state', async () => {
  const f = fixture(async () => response({}));
  await f.worker.ready();
  let replied = false;
  const result = f.listeners.message(
    { type: 'gemini-sync', action: 'state' },
    { id: 'extension', url: 'https://evil.com' },
    () => {
      replied = true;
    }
  );
  assert.equal(result, undefined);
  assert.equal(replied, false);
  const state = await f.worker.command({ action: 'state' });
  assert.equal('token' in state, false);
  assert.equal('cookies' in state, false);
});

test('heartbeat sends refresh state and never throws on network error', async () => {
  let heartbeatBody;
  const f = fixture(async (data, url, options) => {
    if (url.endsWith('/v1/heartbeat')) {
      heartbeatBody = JSON.parse(options.body);
      return response({});
    }
    return response({ syncRequested: false, status: 'good', lastApplied: 4 });
  });
  await f.worker.ready();
  // heartbeat is best-effort; it must not throw even on failure
  await assert.doesNotReject(f.worker.heartbeat());
  assert.ok(heartbeatBody, 'heartbeat body must be sent');
  assert.ok('refresh' in heartbeatBody);
  assert.equal(typeof heartbeatBody.refresh.enabled, 'boolean');
  assert.equal(typeof heartbeatBody.refresh.minutes, 'number');
  assert.equal(typeof heartbeatBody.refresh.lastRefreshAt, 'number');
  assert.equal(typeof heartbeatBody.refresh.nextRefreshAt, 'number');
});

test('heartbeat silently ignores network failures', async () => {
  const f = fixture(async () => {
    throw new Error('offline');
  });
  await f.worker.ready();
  await assert.doesNotReject(f.worker.heartbeat());
});

test('heartbeat is skipped when status is unauthorized', async () => {
  let calls = 0;
  const f = fixture(async (data, url) => {
    if (url.endsWith('/v1/heartbeat')) calls++;
    return response({});
  });
  await f.worker.ready();
  f.data.geminiSync.status = 'unauthorized';
  await f.worker.heartbeat();
  assert.equal(calls, 0);
});

test('fetchProfiles returns sanitized profile list bounded at 100', async () => {
  const serverProfiles = Array.from({ length: 5 }, (_, i) => ({
    profileId: `prof${i}`,
    label: `Profile ${i}`,
    isCurrent: i === 0,
    online: true,
    lastSeen: 1700000000 + i,
    lastAppliedAt: 1700000100 + i,
    status: 'good',
    refresh: {
      enabled: false,
      minutes: 30,
      lastRefreshAt: 0,
      nextRefreshAt: 0,
    },
    token: 'must-be-stripped',
  }));
  const f = fixture(async (data, url) => {
    if (url.endsWith('/v1/profiles')) {
      return response({ profiles: serverProfiles });
    }
    return response({ syncRequested: false, status: 'good', lastApplied: 4 });
  });
  await f.worker.ready();
  const result = await f.worker.command({ action: 'fetchProfiles' });
  assert.ok(Array.isArray(result.profiles));
  assert.equal(result.profiles.length, 5);
  for (const p of result.profiles) {
    assert.equal('token' in p, false);
    assert.ok(p.profileId);
    assert.ok(typeof p.label === 'string');
    assert.ok(typeof p.isCurrent === 'boolean');
    assert.ok(typeof p.online === 'boolean');
    assert.ok(p.refresh && typeof p.refresh.enabled === 'boolean');
  }
});

test('fetchProfiles caps at 100 profiles from server', async () => {
  const many = Array.from({ length: 120 }, (_, i) => ({
    profileId: `p${i}`,
    label: `P${i}`,
    isCurrent: i === 0,
    online: false,
    lastSeen: 0,
    lastAppliedAt: 0,
    status: 'good',
    refresh: {
      enabled: false,
      minutes: 30,
      lastRefreshAt: 0,
      nextRefreshAt: 0,
    },
  }));
  const f = fixture(async (data, url) => {
    if (url.endsWith('/v1/profiles')) return response({ profiles: many });
    return response({ syncRequested: false, status: 'good', lastApplied: 4 });
  });
  await f.worker.ready();
  const result = await f.worker.command({ action: 'fetchProfiles' });
  assert.equal(result.profiles.length, 100);
});

test('fetchProfiles marks unauthorized status when server returns 401', async () => {
  const f = fixture(async (data, url) => {
    if (url.endsWith('/v1/profiles'))
      return { ok: false, status: 401, json: async () => ({}) };
    return response({ syncRequested: false, status: 'good', lastApplied: 4 });
  });
  await f.worker.ready();
  await assert.rejects(f.worker.command({ action: 'fetchProfiles' }));
  assert.equal(f.data.geminiSync.status, 'unauthorized');
});

test('fetchProfiles rejects when not paired', async () => {
  const f = fixture(async () => response({}));
  await f.worker.ready();
  delete f.data.geminiSync.token;
  await assert.rejects(f.worker.command({ action: 'fetchProfiles' }));
});

test('state command exposes refresh timing fields', async () => {
  const f = fixture(async () => response({}));
  await f.worker.ready();
  f.data.geminiSync.lastRefreshAt = 1700001000;
  f.data.geminiSync.nextRefreshAt = 1700002800;
  const state = await f.worker.command({ action: 'state' });
  assert.equal(state.lastRefreshAt, 1700001000);
  assert.equal(state.nextRefreshAt, 1700002800);
});
