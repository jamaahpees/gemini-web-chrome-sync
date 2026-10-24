/* eslint-disable require-jsdoc */
const SESSION_NAMES = new Set([
  'SID',
  'HSID',
  'SSID',
  'APISID',
  'SAPISID',
  'SIDCC',
  '__Secure-1PSID',
  '__Secure-3PSID',
  '__Secure-1PAPISID',
  '__Secure-3PAPISID',
  '__Secure-1PSIDTS',
  '__Secure-3PSIDTS',
  '__Secure-1PSIDCC',
  '__Secure-3PSIDCC',
  '__Secure-1PSIDRTS',
  '__Secure-3PSIDRTS',
  '__Secure-ENID',
  'AEC',
  'NID',
  'SOCS',
  'COMPASS',
  '__Secure-BUCKET',
]);

export function endpointOrigin(input) {
  if (
    input === 'http://100.100.100.100:8787' ||
    input === 'http://100.100.100.100:8787/'
  ) {
    return 'http://100.100.100.100:8787';
  }
  if (
    typeof input !== 'string' ||
    input.length > 253 ||
    !/^https:\/\/[a-zA-Z0-9.-]+(?::8443)?\/?$/.test(input)
  ) {
    throw new Error('Enter an HTTPS Tailscale endpoint.');
  }
  const url = new URL(input);
  if (
    url.protocol !== 'https:' ||
    url.username ||
    url.password ||
    (url.port && url.port !== '8443') ||
    url.pathname !== '/' ||
    url.search ||
    url.hash ||
    !/^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+ts\.net$/.test(url.hostname) ||
    /[?#]/.test(input)
  ) {
    throw new Error(
      'Use https://your-host.your-tailnet.ts.net:8443 (no path or credentials; only port 8443 or default).'
    );
  }
  return url.origin;
}

export function permissionPattern(endpoint) {
  const url = new URL(endpointOrigin(endpoint));
  return url.protocol + '//' + url.hostname + '/*';
}

export function allowedCookie(cookie) {
  return Boolean(
    cookie &&
      [
        'google.com',
        '.google.com',
        'gemini.google.com',
        '.gemini.google.com',
      ].includes(cookie.domain) &&
      SESSION_NAMES.has(cookie.name) &&
      cookie.path === '/' &&
      (!cookie.name.startsWith('__Secure-') || cookie.secure === true) &&
      !cookie.partitionKey &&
      !cookie.incognito &&
      (cookie.storeId === undefined || cookie.storeId === '0')
  );
}

export function cookieIdentity(cookie) {
  return JSON.stringify([
    cookie.domain,
    cookie.path,
    cookie.name,
    cookie.storeId || '0',
  ]);
}

export function snapshotCookies(cookies) {
  const unique = new Map();
  for (const cookie of cookies.filter(allowedCookie)) {
    const item = {
      name: cookie.name,
      value: cookie.value,
      domain: cookie.domain,
      path: cookie.path,
      secure: cookie.secure,
      httpOnly: cookie.httpOnly,
    };
    if (Number.isFinite(cookie.expirationDate)) {
      item.expirationDate = cookie.expirationDate;
    }
    unique.set(cookieIdentity(cookie), item);
  }
  return [...unique.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([, c]) => c);
}

export function isLogout(cookies) {
  return !cookies.some(
    cookie => cookie.name === '__Secure-1PSID' && cookie.value
  );
}

export function retryMinutes(attempt) {
  return Math.min(5, Math.max(1, 2 ** Math.min(attempt, 3)));
}

export function refreshMinutes(value) {
  const minutes = Number(value);
  if (!Number.isInteger(minutes) || minutes < 15 || minutes > 1440) {
    throw new Error('Refresh interval must be 15–1440 minutes.');
  }
  return minutes;
}

export function isGeminiTab(tab) {
  try {
    return (
      !tab.incognito && new URL(tab.url).origin === 'https://gemini.google.com'
    );
  } catch {
    return false;
  }
}

const ALLOWED_PROFILE_FIELDS = [
  'profileId',
  'label',
  'isCurrent',
  'online',
  'lastSeen',
  'lastAppliedAt',
  'status',
  'refresh',
];

const ALLOWED_REFRESH_FIELDS = [
  'enabled',
  'minutes',
  'lastRefreshAt',
  'nextRefreshAt',
];

const KNOWN_PROFILE_STATUSES = new Set([
  'awaiting_sync',
  'good',
  'logged_out',
  'invalid',
  'unauthorized',
  'retrying',
  'account_changed',
  'backend_unavailable',
  'backend_partial_failure',
  'recovery_required',
  'unknown',
]);

export function sanitizeProfile(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const profileId =
    typeof raw.profileId === 'string' &&
    /^[A-Za-z0-9_-]{1,128}$/.test(raw.profileId)
      ? raw.profileId
      : null;
  if (!profileId) return null;
  const label =
    typeof raw.label === 'string' ? raw.label.slice(0, 128) : profileId;
  const isCurrent = raw.isCurrent === true;
  const online = raw.online === true;
  const lastSeen =
    typeof raw.lastSeen === 'number' &&
    Number.isFinite(raw.lastSeen) &&
    raw.lastSeen >= 0
      ? Math.floor(raw.lastSeen)
      : 0;
  const lastAppliedAt =
    typeof raw.lastAppliedAt === 'number' &&
    Number.isFinite(raw.lastAppliedAt) &&
    raw.lastAppliedAt >= 0
      ? Math.floor(raw.lastAppliedAt)
      : 0;
  const status =
    typeof raw.status === 'string' && KNOWN_PROFILE_STATUSES.has(raw.status)
      ? raw.status
      : 'unknown';
  const refresh = sanitizeRefreshState(raw.refresh);
  const out = {
    profileId,
    label,
    isCurrent,
    online,
    lastSeen,
    lastAppliedAt,
    status,
    refresh,
  };
  // Enforce the allowlist — no extra keys from the server pass through.
  for (const key of Object.keys(out)) {
    if (!ALLOWED_PROFILE_FIELDS.includes(key)) delete out[key];
  }
  return out;
}

export function sanitizeRefreshState(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { enabled: false, minutes: 30, lastRefreshAt: 0, nextRefreshAt: 0 };
  }
  const enabled = raw.enabled === true;
  const minutes =
    typeof raw.minutes === 'number' &&
    Number.isInteger(raw.minutes) &&
    raw.minutes >= 15 &&
    raw.minutes <= 1440
      ? raw.minutes
      : 30;
  const lastRefreshAt =
    typeof raw.lastRefreshAt === 'number' &&
    Number.isFinite(raw.lastRefreshAt) &&
    raw.lastRefreshAt >= 0
      ? Math.floor(raw.lastRefreshAt)
      : 0;
  const nextRefreshAt =
    typeof raw.nextRefreshAt === 'number' &&
    Number.isFinite(raw.nextRefreshAt) &&
    raw.nextRefreshAt >= 0
      ? Math.floor(raw.nextRefreshAt)
      : 0;
  const out = { enabled, minutes, lastRefreshAt, nextRefreshAt };
  for (const key of Object.keys(out)) {
    if (!ALLOWED_REFRESH_FIELDS.includes(key)) delete out[key];
  }
  return out;
}

export function sanitizeProfiles(raw) {
  if (!Array.isArray(raw)) return [];
  return raw.slice(0, 100).map(sanitizeProfile).filter(Boolean);
}
