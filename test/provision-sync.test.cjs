/* global __dirname */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { execFileSync } = require('node:child_process');
const path = require('node:path');

test('provision rejects untrusted target names without touching Docker', () => {
  const script = path.resolve(__dirname, '../deploy/provision.py');
  assert.ok(fs.existsSync(script), 'provision command exists');
  assert.throws(
    () =>
      execFileSync('python', [script, '--name', '../escape', '--dry-run'], {
        encoding: 'utf8',
      }),
    /Command failed/
  );
});

test('dry run describes dedicated backend and contains no credentials', () => {
  const output = execFileSync(
    'python',
    [
      path.resolve(__dirname, '../deploy/provision.py'),
      '--name',
      'profile-b',
      '--dry-run',
    ],
    { encoding: 'utf8' }
  );
  const result = JSON.parse(output);
  assert.equal(result.container, 'gemini-sync-profile-b');
  assert.equal(result.prefix, 'gweb-profile-b');
  assert.equal(result.isolated, true);
  assert.ok(!output.includes('adminToken'));
});
