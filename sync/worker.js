/* eslint-disable require-jsdoc */
import {
  allowedCookie,
  endpointOrigin,
  isGeminiTab,
  isLogout,
  permissionPattern,
  refreshMinutes,
  retryMinutes,
  sanitizeProfiles,
  snapshotCookies,
} from './helpers.js';

const KEY = 'geminiSync';
const PREFIX = 'gemini-sync-';
const BLOCKED = new Set(['account_changed', 'unauthorized']);

export function createSyncWorker(api, fetcher = fetch) {
  let dirty = false;
  let running = false;
  let timer;
  let queue = Promise.resolve();
  const ready = api.storage.local.setAccessLevel({
    accessLevel: 'TRUSTED_CONTEXTS',
  });
  const read = async () => (await api.storage.local.get(KEY))[KEY] || {};
  const save = state => api.storage.local.set({ [KEY]: state });
  const serial = task => {
    const result = queue.then(() => ready).then(task);
    queue = result.catch(() => {});
    return result;
  };
  const alarm = (name, delayInMinutes) =>
    api.alarms.create(PREFIX + name, { delayInMinutes });

  async function request(state, path, method = 'GET', body) {
    const origin = endpointOrigin(state.endpoint);
    if (
      !(await api.permissions.contains({
        origins: [permissionPattern(origin)],
      }))
    ) {
      throw new Error('permission');
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 25000);
    try {
      const response = await fetcher(origin + path, {
        method,
        redirect: 'error',
        credentials: 'omit',
        cache: 'no-store',
        headers: {
          'Content-Type': 'application/json',
          ...(state.token ? { Authorization: 'Bearer ' + state.token } : {}),
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        signal: controller.signal,
      });
      if (response.status === 401 || response.status === 403) {
        throw new Error('unauthorized');
      }
      if (!response.ok) throw new Error('network');
      return method === 'DELETE' ? {} : await response.json();
    } finally {
      clearTimeout(timeout);
    }
  }

  async function failure(state, error) {
    state.status =
      error.message === 'unauthorized'
        ? 'unauthorized'
        : state.accountChanged
          ? 'account_changed'
          : 'retrying';
    state.needsSync = true;
    const delay = retryMinutes(state.attempt || 0);
    state.attempt = (state.attempt || 0) + 1;
    await save(state);
    if (
      state.status !== 'unauthorized' &&
      !(await api.alarms.get(PREFIX + 'retry'))
    ) {
      await alarm('retry', delay);
    }
  }

  async function sendSnapshot() {
    const state = await read();
    if (!state.token || state.status === 'unauthorized') return;
    try {
      if (
        !(await api.permissions.contains({
          origins: ['https://gemini.google.com/*', 'https://google.com/*'],
        }))
      ) {
        throw new Error('permission');
      }
      let cookies = snapshotCookies(
        await api.cookies.getAll({
          url: 'https://gemini.google.com/',
          storeId: '0',
        })
      );
      const logout = isLogout(cookies);
      if (
        (state.accountChanged || state.status === 'account_changed') &&
        !logout
      )
        return;
      if (state.status === 'account_changed') state.accountChanged = true;
      if (logout) cookies = [];
      const digest = await crypto.subtle.digest(
        'SHA-256',
        new TextEncoder().encode(JSON.stringify({ cookies, logout }))
      );
      const hash = Array.from(new Uint8Array(digest), byte =>
        byte.toString(16).padStart(2, '0')
      ).join('');
      if (
        state.invalidHash === hash ||
        (state.lastAcknowledgedHash === hash && !state.needsSync)
      )
        return;
      const sequence =
        state.pendingHash === hash &&
        Number.isSafeInteger(state.pendingSequence)
          ? state.pendingSequence
          : (state.sequence || 0) + 1;
      if (!Number.isSafeInteger(sequence)) throw new Error('sequence');
      state.sequence = sequence;
      state.pendingHash = hash;
      state.pendingSequence = sequence;
      state.needsSync = true;
      await save(state);
      // The durable retry survives a worker being suspended after dispatch.
      await alarm('retry', 1);
      const result = await request(state, '/v1/snapshot', 'POST', {
        sequence,
        cookies,
        logout,
      });
      if (
        !['applied', 'unchanged', 'invalid', 'account_changed'].includes(
          result.status
        ) ||
        result.sequence !== sequence
      )
        throw new Error('response');
      state.status = result.status;
      if (result.status === 'account_changed') state.accountChanged = true;
      if (result.status === 'invalid') state.invalidHash = hash;
      else delete state.invalidHash;
      if (result.status === 'applied' || result.status === 'unchanged') {
        state.lastAcknowledgedHash = hash;
      }
      state.needsSync = false;
      delete state.pendingHash;
      delete state.pendingSequence;
      state.attempt = 0;
      if (result.status === 'applied' || result.status === 'unchanged') {
        state.lastApplied = Date.now();
      }
      await save(state);
      await api.alarms.clear(PREFIX + 'retry');
    } catch (error) {
      await api.alarms.clear(PREFIX + 'retry');
      await failure(state, error);
    }
  }

  function syncNow() {
    dirty = true;
    if (running) return queue;
    running = true;
    return serial(async () => {
      try {
        while (dirty) {
          dirty = false;
          await sendSnapshot();
        }
      } finally {
        running = false;
      }
    });
  }

  function changed(change) {
    if (!allowedCookie(change.cookie)) return;
    dirty = true;
    // Replacements arrive as remove/add pairs. Never infer logout from an event.
    void alarm('debounce', 1).catch(() => {});
    clearTimeout(timer);
    timer = setTimeout(() => void syncNow().catch(() => {}), 5000);
  }

  async function status() {
    const state = await read();
    if (!state.token || BLOCKED.has(state.status)) return;
    try {
      const result = await request(state, '/v1/status');
      if (
        typeof result.syncRequested !== 'boolean' ||
        typeof result.status !== 'string' ||
        !Number.isFinite(result.lastApplied)
      )
        throw new Error('response');
      if (BLOCKED.has(result.status)) {
        state.status = result.status;
        if (result.status === 'account_changed') state.accountChanged = true;
        await save(state);
      } else if (
        (result.syncRequested ||
          result.status === 'invalid' ||
          state.needsSync) &&
        !(await api.alarms.get(PREFIX + 'retry'))
      ) {
        state.needsSync = true;
        await save(state);
        dirty = true;
      }
    } catch (error) {
      await failure(state, error);
    }
  }

  async function refresh() {
    const state = await read();
    if (!state.token || !state.refreshEnabled || BLOCKED.has(state.status))
      return;
    const tab = await api.tabs.get(state.refreshTabId).catch(() => null);
    if (!tab || tab.active || !isGeminiTab(tab)) return;
    const now = Math.floor(Date.now() / 1000);
    state.lastRefreshAt = now;
    state.nextRefreshAt = now + refreshMinutes(state.refreshMinutes || 30) * 60;
    await save(state);
    await api.tabs.reload(tab.id);
  }

  async function heartbeat() {
    const state = await read();
    if (!state.token || state.status === 'unauthorized') return;
    try {
      const refreshPayload = {
        enabled: Boolean(state.refreshEnabled),
        minutes: state.refreshMinutes || 30,
        lastRefreshAt: state.lastRefreshAt || 0,
        nextRefreshAt: state.nextRefreshAt || 0,
      };
      await request(state, '/v1/heartbeat', 'POST', {
        refresh: refreshPayload,
      });
    } catch {
      // Heartbeat is best-effort telemetry; errors do not affect sync state.
    }
  }

  async function configureAlarms() {
    if (!(await api.alarms.get(PREFIX + 'status'))) {
      await api.alarms.create(PREFIX + 'status', { periodInMinutes: 1 });
    }
    if (!(await api.alarms.get(PREFIX + 'reconcile'))) {
      await api.alarms.create(PREFIX + 'reconcile', { periodInMinutes: 5 });
    }
    const state = await read();
    const minutes = refreshMinutes(state.refreshMinutes || 30);
    if (state.refreshEnabled && !(await api.alarms.get(PREFIX + 'refresh'))) {
      if (!state.nextRefreshAt) {
        state.nextRefreshAt = Math.floor(Date.now() / 1000) + minutes * 60;
        await save(state);
      }
      await api.alarms.create(PREFIX + 'refresh', {
        periodInMinutes: minutes,
      });
    }
  }

  async function command(message) {
    return serial(async () => {
      const state = await read();
      switch (message.action) {
        case 'state': {
          const tabs = await api.tabs.query({
            url: 'https://gemini.google.com/*',
          });
          return {
            paired: Boolean(state.token),
            endpoint: state.endpoint || '',
            status: state.status || 'not_paired',
            lastApplied: state.lastApplied || 0,
            refreshEnabled: Boolean(state.refreshEnabled),
            refreshMinutes: state.refreshMinutes || 30,
            refreshTabId: state.refreshTabId,
            lastRefreshAt: state.lastRefreshAt || 0,
            nextRefreshAt: state.nextRefreshAt || 0,
            tabs: tabs
              .filter(isGeminiTab)
              .map(tab => ({ id: tab.id, active: tab.active })),
          };
        }
        case 'getCleanup':
        case 'setCleanup': {
          if (!state.token) throw new Error('Pair this profile first.');
          const setting = message.action === 'setCleanup';
          if (
            setting &&
            (typeof message.enabled !== 'boolean' ||
              Object.keys(message).some(
                key => !['type', 'action', 'enabled'].includes(key)
              ))
          )
            throw new Error('Invalid cleanup preference.');
          let result;
          try {
            result = await request(
              state,
              '/v1/cleanup',
              setting ? 'POST' : 'GET',
              setting ? { enabled: message.enabled } : undefined
            );
          } catch (error) {
            if (error.message === 'unauthorized') {
              state.status = 'unauthorized';
              await save(state);
              throw new Error('unauthorized');
            }
            throw new Error('Cleanup request failed. Check server connection.');
          }
          if (
            !result ||
            Array.isArray(result) ||
            typeof result.enabled !== 'boolean' ||
            typeof result.multiTurn !== 'boolean' ||
            typeof result.temporaryChat !== 'boolean' ||
            result.bestEffort !== true
          )
            throw new Error('Invalid cleanup response.');
          return setting
            ? { ok: true }
            : {
                enabled: result.enabled,
                multiTurn: result.multiTurn,
                temporaryChat: result.temporaryChat,
                bestEffort: true,
              };
        }
        case 'fetchProfiles': {
          if (!state.token) throw new Error('Pair this profile first.');
          let result;
          try {
            result = await request(state, '/v1/profiles');
          } catch (error) {
            if (error.message === 'unauthorized') {
              state.status = 'unauthorized';
              await save(state);
            }
            throw error;
          }
          if (!Array.isArray(result.profiles))
            throw new Error('Invalid profiles response.');
          return { profiles: sanitizeProfiles(result.profiles) };
        }
        case 'importConnection': {
          if (state.token)
            throw new Error('Disconnect the current profile first.');
          const config = message.connection;
          if (
            !config ||
            typeof config !== 'object' ||
            typeof config.deviceId !== 'string' ||
            !/^[A-Za-z0-9_-]{4,128}$/.test(config.deviceId) ||
            typeof config.token !== 'string' ||
            !/^[A-Za-z0-9_-]{40,128}$/.test(config.token)
          ) {
            throw new Error('Invalid connection configuration.');
          }
          const endpoint = endpointOrigin(config.endpoint);
          const result = await request(
            { endpoint, token: config.token },
            '/v1/status'
          );
          if (
            typeof result.syncRequested !== 'boolean' ||
            typeof result.status !== 'string' ||
            !Number.isSafeInteger(result.lastApplied) ||
            result.lastApplied < 0
          ) {
            throw new Error('Invalid server response.');
          }
          await save({
            endpoint,
            deviceId: config.deviceId,
            token: config.token,
            sequence: result.lastApplied,
            status: 'paired',
            needsSync: true,
            refreshEnabled: false,
          });
          await alarm('retry', 1);
          return { ok: true };
        }
        case 'pair': {
          if (state.token)
            throw new Error('Unpair this profile before pairing again.');
          const endpoint = endpointOrigin(message.endpoint);
          if (
            typeof message.code !== 'string' ||
            !/^[A-Za-z0-9_-]{4,128}$/.test(message.code)
          ) {
            throw new Error('Enter a valid pairing code.');
          }
          const result = await request({ endpoint }, '/v1/pair', 'POST', {
            code: message.code,
          });
          if (
            typeof result.deviceId !== 'string' ||
            !result.deviceId ||
            typeof result.token !== 'string' ||
            !result.token ||
            result.token.length > 4096 ||
            /[\r\n]/.test(result.token)
          )
            throw new Error('Pairing failed.');
          await save({
            endpoint,
            deviceId: result.deviceId,
            token: result.token,
            sequence: 0,
            status: 'paired',
            needsSync: true,
            refreshEnabled: false,
          });
          await alarm('retry', 1);
          return { ok: true };
        }
        case 'unpair':
          if (state.token) await request(state, '/v1/pair', 'DELETE');
          await api.storage.local.remove(KEY);
          await api.alarms.clear(PREFIX + 'retry');
          await api.alarms.clear(PREFIX + 'refresh');
          return { ok: true };
        case 'refresh': {
          if (!state.token) throw new Error('Pair this profile first.');
          const minutes = refreshMinutes(message.minutes);
          if (message.enabled) {
            const tab = await api.tabs.get(message.tabId);
            if (!isGeminiTab(tab))
              throw new Error('Select an existing Gemini tab.');
          }
          state.refreshEnabled = message.enabled === true;
          state.refreshMinutes = minutes;
          state.refreshTabId = message.tabId;
          if (state.refreshEnabled) {
            state.nextRefreshAt = Math.floor(Date.now() / 1000) + minutes * 60;
          } else {
            state.nextRefreshAt = 0;
            state.lastRefreshAt = state.lastRefreshAt || 0;
          }
          await save(state);
          await api.alarms.clear(PREFIX + 'refresh');
          await configureAlarms();
          return { ok: true };
        }
        default:
          throw new Error('Unknown sync action.');
      }
    });
  }

  api.cookies.onChanged.addListener(changed);
  api.alarms.onAlarm.addListener(event => {
    if (!event.name.startsWith(PREFIX)) return;
    const name = event.name.slice(PREFIX.length);
    if (name === 'status') {
      void serial(status)
        .then(() => {
          if (dirty) return syncNow();
        })
        .then(() => serial(heartbeat))
        .catch(() => {});
    } else if (name === 'refresh') {
      void serial(refresh).catch(() => {});
    } else {
      void syncNow().catch(() => {});
    }
  });
  api.runtime.onStartup.addListener(() => {
    void serial(configureAlarms)
      .then(syncNow)
      .catch(() => {});
  });
  api.runtime.onInstalled.addListener(() => {
    api.contextMenus.create({
      id: PREFIX + 'settings',
      title: 'Gemini sync settings',
      contexts: ['action'],
    });
    void serial(configureAlarms).catch(() => {});
  });
  api.contextMenus.onClicked.addListener(info => {
    if (info.menuItemId === PREFIX + 'settings') {
      void api.tabs.create({ url: api.runtime.getURL('sync/settings.html') });
    }
  });
  api.runtime.onMessage.addListener((message, sender, respond) => {
    if (
      message?.type !== 'gemini-sync' ||
      sender.id !== api.runtime.id ||
      sender.url !== api.runtime.getURL('sync/settings.html') ||
      sender.tab?.incognito
    )
      return;
    command(message).then(
      result => {
        respond(result);
        if (message.action === 'pair' || message.action === 'importConnection')
          void syncNow().catch(() => {});
      },
      error =>
        respond({
          ok: false,
          error: error?.message || 'Sync action failed.',
        })
    );
    return true;
  });
  void serial(configureAlarms)
    .then(async () => {
      if ((await read()).needsSync) return syncNow();
    })
    .catch(() => {});
  return { command, syncNow, heartbeat, ready: () => queue };
}
