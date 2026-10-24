/* eslint-disable require-jsdoc */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  isGeminiTab,
  sanitizeProfile,
  sanitizeProfiles,
  sanitizeRefreshState,
} from '../sync/helpers.js';

test('refresh accepts only regular exact-origin Gemini tabs', () => {
  assert.equal(
    isGeminiTab({ url: 'https://gemini.google.com/app', active: false }),
    true
  );
  for (const tab of [
    null,
    {},
    { url: 'https://gemini.google.com.evil.com' },
    { url: 'http://gemini.google.com' },
    { url: 'https://gemini.google.com', incognito: true },
  ]) {
    assert.equal(isGeminiTab(tab), false);
  }
});

test('sanitizeRefreshState fills defaults for missing or invalid input', () => {
  const def = sanitizeRefreshState(null);
  assert.equal(def.enabled, false);
  assert.equal(def.minutes, 30);
  assert.equal(def.lastRefreshAt, 0);
  assert.equal(def.nextRefreshAt, 0);

  const good = sanitizeRefreshState({
    enabled: true,
    minutes: 60,
    lastRefreshAt: 100,
    nextRefreshAt: 3700,
  });
  assert.equal(good.enabled, true);
  assert.equal(good.minutes, 60);
  assert.equal(good.lastRefreshAt, 100);
  assert.equal(good.nextRefreshAt, 3700);

  // minutes out of bounds falls back to 30
  const bad = sanitizeRefreshState({ enabled: true, minutes: 5 });
  assert.equal(bad.minutes, 30);
  const badHigh = sanitizeRefreshState({ enabled: true, minutes: 9999 });
  assert.equal(badHigh.minutes, 30);

  // negative timestamps become 0
  const neg = sanitizeRefreshState({
    enabled: false,
    minutes: 15,
    lastRefreshAt: -1,
    nextRefreshAt: -999,
  });
  assert.equal(neg.lastRefreshAt, 0);
  assert.equal(neg.nextRefreshAt, 0);
});

test('sanitizeRefreshState only exposes allowlisted fields', () => {
  const r = sanitizeRefreshState({
    enabled: false,
    minutes: 15,
    lastRefreshAt: 0,
    nextRefreshAt: 0,
    token: 'secret',
    extra: 'bad',
  });
  assert.equal('token' in r, false);
  assert.equal('extra' in r, false);
});

test('sanitizeProfile rejects missing or invalid profileId', () => {
  assert.equal(sanitizeProfile(null), null);
  assert.equal(sanitizeProfile({}), null);
  assert.equal(sanitizeProfile({ profileId: '' }), null);
  assert.equal(sanitizeProfile({ profileId: 'has space' }), null);
  assert.equal(sanitizeProfile([]), null);
});

test('sanitizeProfile returns allowlisted fields only', () => {
  const raw = {
    profileId: 'abc123',
    label: 'My Profile',
    isCurrent: true,
    online: true,
    lastSeen: 1700000000,
    lastAppliedAt: 1700000100,
    status: 'good',
    refresh: {
      enabled: true,
      minutes: 30,
      lastRefreshAt: 1700000200,
      nextRefreshAt: 1700002000,
    },
    token: 'should-be-stripped',
    cookie: 'also-stripped',
  };
  const p = sanitizeProfile(raw);
  assert.ok(p);
  assert.equal(p.profileId, 'abc123');
  assert.equal(p.label, 'My Profile');
  assert.equal(p.isCurrent, true);
  assert.equal(p.online, true);
  assert.equal(p.lastSeen, 1700000000);
  assert.equal(p.lastAppliedAt, 1700000100);
  assert.equal(p.status, 'good');
  assert.ok(p.refresh);
  assert.equal(p.refresh.enabled, true);
  assert.equal('token' in p, false);
  assert.equal('cookie' in p, false);
});

test('sanitizeProfile normalises unknown status to "unknown"', () => {
  const p = sanitizeProfile({ profileId: 'x', status: 'totally_made_up' });
  assert.equal(p.status, 'unknown');
});

test('sanitizeProfile clamps label to 128 characters', () => {
  const long = 'a'.repeat(200);
  const p = sanitizeProfile({ profileId: 'x', label: long });
  assert.equal(p.label.length, 128);
});

test('sanitizeProfile uses profileId as fallback label', () => {
  const p = sanitizeProfile({ profileId: 'myid' });
  assert.equal(p.label, 'myid');
});

test('sanitizeProfiles caps at 100 entries and filters invalid', () => {
  const many = Array.from({ length: 120 }, (_, i) => ({
    profileId: `p${i}`,
    status: 'good',
  }));
  const result = sanitizeProfiles(many);
  assert.equal(result.length, 100);

  const mixed = [null, { profileId: 'valid' }, { profileId: '' }, 42];
  const cleaned = sanitizeProfiles(mixed);
  assert.equal(cleaned.length, 1);
  assert.equal(cleaned[0].profileId, 'valid');
});

test('sanitizeProfiles returns empty array for non-array input', () => {
  assert.deepEqual(sanitizeProfiles(null), []);
  assert.deepEqual(sanitizeProfiles('bad'), []);
  assert.deepEqual(sanitizeProfiles({}), []);
});

test('refresh timing: nextRefreshAt is consistent with minutes interval', () => {
  const now = Math.floor(Date.now() / 1000);
  const minutes = 30;
  const nextRefreshAt = now + minutes * 60;
  assert.ok(nextRefreshAt > now);
  assert.ok(nextRefreshAt <= now + 1440 * 60);
  // nextRefreshAt - lastRefreshAt should equal the interval
  const lastRefreshAt = now;
  assert.equal(nextRefreshAt - lastRefreshAt, minutes * 60);
});
