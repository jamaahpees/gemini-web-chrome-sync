/* eslint-disable require-jsdoc */
import { endpointOrigin, permissionPattern } from './helpers.js';

const element = id => document.getElementById(id);
const message = action =>
  chrome.runtime.sendMessage({ type: 'gemini-sync', ...action });
const knownStatuses = new Set([
  'not_paired',
  'paired',
  'applied',
  'unchanged',
  'retrying',
  'invalid',
  'account_changed',
  'unauthorized',
]);

function formatTimestamp(seconds) {
  if (!seconds) return 'never';
  return new Date(seconds * 1000).toLocaleString();
}

function formatRemaining(nextRefreshAt) {
  if (!nextRefreshAt) return '';
  const secs = Math.max(0, nextRefreshAt - Math.floor(Date.now() / 1000));
  if (secs === 0) return ' (due now)';
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m > 0 ? ` (in ${m}m ${s}s)` : ` (in ${s}s)`;
}

async function render() {
  const state = await message({ action: 'state' });
  if (!state || state.ok === false) throw new Error('Settings unavailable.');
  const status = knownStatuses.has(state.status)
    ? state.status.replaceAll('_', ' ')
    : 'unknown';
  let statusText = `Status: ${status}. Last accepted snapshot: ${state.lastApplied ? new Date(state.lastApplied).toLocaleString() : 'never'}.`;
  if (state.refreshEnabled) {
    statusText +=
      ` Last tab refresh: ${formatTimestamp(state.lastRefreshAt)}.` +
      ` Next: ${formatTimestamp(state.nextRefreshAt)}${formatRemaining(state.nextRefreshAt)}.`;
  }
  element('status').textContent = statusText;
  element('endpoint').value = state.endpoint;
  element('pair-button').disabled = state.paired;
  element('unpair').disabled = !state.paired;
  const oneClick = element('one-click-section');
  if (oneClick) oneClick.style.display = state.paired ? 'none' : '';
  element('refresh-enabled').checked = state.refreshEnabled;
  element('minutes').value = state.refreshMinutes;
  element('tab').replaceChildren();
  for (const tab of state.tabs) {
    const option = document.createElement('option');
    option.value = tab.id;
    option.textContent = `Tab ${tab.id}${tab.active ? ' (active — will not reload)' : ''}`;
    option.selected = tab.id === state.refreshTabId;
    element('tab').append(option);
  }
  if (state.paired) {
    await renderProfiles();
  }
}

async function renderProfiles() {
  const profilesSection = element('profiles-section');
  if (!profilesSection) return;
  profilesSection.style.display = 'block';
  const tbody = element('profiles-tbody');
  if (!tbody) return;
  let result;
  try {
    result = await message({ action: 'fetchProfiles' });
  } catch {
    tbody.innerHTML =
      '<tr><td colspan="7">Could not load profiles. Check server connection.</td></tr>';
    return;
  }
  if (!result || result.ok === false || !Array.isArray(result.profiles)) {
    tbody.innerHTML = '<tr><td colspan="7">Profiles unavailable.</td></tr>';
    return;
  }
  if (result.profiles.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7">No profiles found.</td></tr>';
    return;
  }
  tbody.replaceChildren();
  for (const p of result.profiles) {
    const tr = document.createElement('tr');
    const syncStatus = knownStatuses.has(p.status)
      ? p.status.replaceAll('_', ' ')
      : p.status || 'unknown';
    const refreshInfo = p.refresh?.enabled
      ? `${p.refresh.minutes}min — next: ${formatTimestamp(p.refresh.nextRefreshAt)}`
      : 'off';
    tr.innerHTML = `
      <td>${escapeHtml(p.label)}${p.isCurrent ? ' ★' : ''}</td>
      <td>${p.isCurrent ? 'Yes' : 'No'}</td>
      <td>${p.online ? '🟢 online' : '⚫ offline'}</td>
      <td>${syncStatus}</td>
      <td>${formatTimestamp(p.lastSeen)}</td>
      <td>${formatTimestamp(p.lastAppliedAt)}</td>
      <td>${refreshInfo}</td>
    `;
    tbody.append(tr);
  }
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

async function finish(action) {
  const result = await message(action);
  if (!result?.ok) {
    throw new Error(result?.error || 'Sync action failed.');
  }
  await render();
}

function failed(error) {
  element('status').textContent = error.message;
}

const defaultConnection = fetch(
  chrome.runtime.getURL('sync/default-connection.json')
)
  .then(response => (response.ok ? response.json() : null))
  .catch(() => null);

// Request permissions directly in the click gesture, before awaiting file I/O.
element('one-click-connect')?.addEventListener('click', async () => {
  try {
    element('one-click-connect').disabled = true;
    element('one-click-connect').textContent = 'Connecting...';
    const granted = await chrome.permissions.request({
      origins: [
        'http://100.100.100.100/*',
        'https://your-node.your-tailnet.ts.net/*',
        'https://gemini.google.com/*',
        'https://google.com/*',
      ],
    });
    if (!granted) throw new Error('Permissions were not granted by user.');
    const connection = await defaultConnection;
    if (!connection)
      throw new Error(
        'Private connection configuration is unavailable. Reload the current extension package.'
      );
    endpointOrigin(connection.endpoint);
    await finish({ action: 'importConnection', connection });
  } catch (error) {
    element('one-click-connect').disabled = false;
    element('one-click-connect').textContent =
      '⚡ Aktifkan Sinkronisasi Sekarang';
    failed(new Error(error?.message || '1-Click connection failed.'));
  }
});

let pendingConnection;

function parseConnectionText(raw) {
  const config = JSON.parse(raw);
  endpointOrigin(config.endpoint);
  if (typeof config.token !== 'string' || typeof config.deviceId !== 'string') {
    throw new Error('Invalid connection JSON.');
  }
  return config;
}

function setPendingConnection(config, sourceLabel) {
  pendingConnection = config;
  element('import-connection').disabled = false;
  element('clipboard-hint').textContent = `Ready (${sourceLabel})`;
  element('clipboard-hint').style.color = '#16a34a';
}

element('connection-json').addEventListener('input', () => {
  const text = element('connection-json').value.trim();
  if (!text) {
    pendingConnection = undefined;
    element('import-connection').disabled = true;
    element('clipboard-hint').textContent = '';
    return;
  }
  try {
    const config = parseConnectionText(text);
    setPendingConnection(config, 'from text box');
  } catch {
    pendingConnection = undefined;
    element('import-connection').disabled = true;
    element('clipboard-hint').textContent = 'Invalid JSON';
    element('clipboard-hint').style.color = '#dc2626';
  }
});

element('paste-clipboard').addEventListener('click', async () => {
  try {
    const text = (await navigator.clipboard.readText()).trim();
    if (!text) {
      throw new Error('Clipboard is empty.');
    }
    const config = parseConnectionText(text);
    element('connection-json').value = text;
    setPendingConnection(config, 'from clipboard');
  } catch (error) {
    failed(
      new Error(
        error?.message || 'Could not read valid connection JSON from clipboard.'
      )
    );
  }
});

element('connection-file').addEventListener('change', async () => {
  pendingConnection = undefined;
  element('import-connection').disabled = true;
  element('clipboard-hint').textContent = '';
  try {
    const file = element('connection-file').files[0];
    if (!file || file.size > 8192)
      throw new Error('Choose a connection JSON smaller than 8 KB.');
    const config = parseConnectionText(await file.text());
    element('connection-json').value = JSON.stringify(config);
    setPendingConnection(config, 'from file');
  } catch {
    failed(new Error('Connection file could not be read.'));
  }
});

element('import-connection').addEventListener('click', async () => {
  try {
    if (!pendingConnection) return;
    const endpoint = endpointOrigin(pendingConnection.endpoint);
    const granted = await chrome.permissions.request({
      origins: [
        permissionPattern(endpoint),
        'https://gemini.google.com/*',
        'https://google.com/*',
      ],
    });
    if (!granted) throw new Error('Permissions were not granted.');
    await finish({ action: 'importConnection', connection: pendingConnection });
    pendingConnection = undefined;
    element('connection-file').value = '';
    element('connection-json').value = '';
    element('clipboard-hint').textContent = '';
    element('import-connection').disabled = true;
  } catch (error) {
    failed(
      new Error(
        error?.message ||
          'Connection failed. Check Tailscale and the private connection file.'
      )
    );
  }
});

element('pair').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    const endpoint = endpointOrigin(element('endpoint').value.trim());
    // Permission request must remain in the explicit submit gesture.
    const granted = await chrome.permissions.request({
      origins: [
        permissionPattern(endpoint),
        'https://gemini.google.com/*',
        'https://google.com/*',
      ],
    });
    if (!granted) throw new Error('Permissions were not granted.');
    const code = element('code').value;
    element('code').value = '';
    await finish({ action: 'pair', endpoint, code });
  } catch (error) {
    failed(error);
  }
});

element('unpair').addEventListener('click', async () => {
  if (!confirm('Revoke this profile\u2019s server pairing?')) return;
  try {
    await finish({ action: 'unpair' });
  } catch (error) {
    failed(error);
  }
});

element('refresh').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    await finish({
      action: 'refresh',
      enabled: element('refresh-enabled').checked,
      minutes: Number(element('minutes').value),
      tabId: Number(element('tab').value),
    });
  } catch (error) {
    failed(error);
  }
});

element('refresh-status-btn')?.addEventListener('click', async () => {
  try {
    await renderProfiles();
  } catch (error) {
    failed(error);
  }
});

// Auto-poll profiles table every 30 seconds while settings page is open.
let pollTimer;
function startPoll() {
  pollTimer = setInterval(async () => {
    try {
      await renderProfiles();
    } catch {
      // Silently ignore poll errors.
    }
  }, 30000);
}
function stopPoll() {
  clearInterval(pollTimer);
}
window.addEventListener('unload', stopPoll);

if (chrome.extension.inIncognitoContext) {
  document.querySelectorAll('button').forEach(button => {
    button.disabled = true;
  });
  element('status').textContent = 'Sync is unavailable in incognito.';
} else {
  void render()
    .then(() => startPoll())
    .catch(() =>
      failed(
        new Error(
          'Settings unavailable. Reopen from the extension toolbar menu.'
        )
      )
    );
}
