import assert from 'node:assert/strict';
import test from 'node:test';

import { HeaderstringFormat } from '../interface/lib/headerstringFormat.js';
import { JsonFormat } from '../interface/lib/jsonFormat.js';
import { NetscapeFormat } from '../interface/lib/netscapeFormat.js';

test('JsonFormat - format and parse cookies', () => {
  const cookiesMap = {
    c1: {
      cookie: {
        name: 'session',
        value: 'xyz123',
        domain: '.example.com',
        path: '/',
        secure: true,
        httpOnly: true,
        sameSite: 'lax',
      },
    },
    c2: {
      cookie: {
        name: 'theme',
        value: 'dark',
        domain: 'example.com',
        path: '/app',
        secure: false,
        httpOnly: false,
      },
    },
  };

  const formattedJson = JsonFormat.format(cookiesMap);
  assert.ok(typeof formattedJson === 'string');

  const parsed = JsonFormat.parse(formattedJson);
  assert.equal(Array.isArray(parsed), true);
  assert.equal(parsed.length, 2);
  assert.equal(parsed[0].name, 'session');
  assert.equal(parsed[0].value, 'xyz123');
  assert.equal(parsed[1].name, 'theme');
  assert.equal(parsed[1].value, 'dark');
});

test('HeaderstringFormat - format and parse header string', () => {
  const cookiesMap = {
    c1: { cookie: { name: 'user_id', value: '42' } },
    c2: { cookie: { name: 'logged_in', value: 'true' } },
  };

  const formattedHeader = HeaderstringFormat.format(cookiesMap);
  assert.equal(formattedHeader, 'user_id=42;logged_in=true');

  const parsed = HeaderstringFormat.parse(
    'user_id=42; logged_in=true; token=abc=123'
  );
  assert.equal(Array.isArray(parsed), true);
  assert.equal(parsed.length, 3);
  assert.equal(parsed[0].name, 'user_id');
  assert.equal(parsed[0].value, '42');
  assert.equal(parsed[1].name, 'logged_in');
  assert.equal(parsed[1].value, 'true');
  assert.equal(parsed[2].name, 'token');
  assert.equal(parsed[2].value, 'abc=123');
});

test('NetscapeFormat - format and parse Netscape cookies', () => {
  const cookiesMap = {
    c1: {
      cookie: {
        name: 'session',
        value: 'abc',
        domain: '.example.com',
        path: '/',
        secure: true,
        expirationDate: 1900000000,
        hostOnly: false,
      },
    },
  };

  const formattedNetscape = NetscapeFormat.format(cookiesMap);
  assert.ok(formattedNetscape.includes('# Netscape HTTP Cookie File'));
  assert.ok(
    formattedNetscape.includes(
      '.example.com\tTRUE\t/\tTRUE\t1900000000\tsession\tabc'
    )
  );

  const parsed = NetscapeFormat.parse(formattedNetscape);
  assert.equal(Array.isArray(parsed), true);
  assert.equal(parsed.length, 1);
  assert.equal(parsed[0].name, 'session');
  assert.equal(parsed[0].value, 'abc');
  assert.equal(parsed[0].domain, '.example.com');
  assert.equal(parsed[0].path, '/');
  assert.equal(parsed[0].secure, true);
  assert.equal(parsed[0].expiration, '1900000000');
});
