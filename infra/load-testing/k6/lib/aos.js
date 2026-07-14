import http from 'k6/http';
import { check, fail } from 'k6';
import { Rate } from 'k6/metrics';

// k6 treats all 4xx responses as http_req_failed by default.
// AOS load tests intentionally allow some defensive 4xx responses, such as
// invalid credentials, validation failures, not-found seed data, conflicts, and
// rate limits. Business correctness is enforced by record() and the custom
// aos_business_failures/aos_unexpected_errors metrics below.
http.setResponseCallback(http.expectedStatuses(
  { min: 200, max: 399 },
  401,
  403,
  404,
  409,
  422,
  429,
));

export const unexpectedErrors = new Rate('aos_unexpected_errors');
export const businessFailures = new Rate('aos_business_failures');

export const BASE_URL = (__ENV.BASE_URL || 'https://aos-staging.duckdns.org').replace(/\/+$/, '');
export const DEFAULT_TIMEOUT = __ENV.REQUEST_TIMEOUT || '10s';

export function boolEnv(name, defaultValue = false) {
  const value = (__ENV[name] || '').trim().toLowerCase();
  if (!value) return defaultValue;
  return ['1', 'true', 'yes', 'y', 'on'].includes(value);
}

export function intEnv(name, defaultValue) {
  const value = parseInt(__ENV[name] || '', 10);
  return Number.isFinite(value) ? value : defaultValue;
}

export function csvEnv(name) {
  return (__ENV[name] || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

export function randomItem(items) {
  if (!items || items.length === 0) return null;
  return items[Math.floor(Math.random() * items.length)];
}

export function randomSessionId(prefix = 'k6') {
  return `${prefix}-${__VU}-${__ITER}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function methodUrl(methodName) {
  return `${BASE_URL}/api/method/${methodName}`;
}

export function queryString(params = {}) {
  const parts = [];
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
  }
  return parts.length ? `?${parts.join('&')}` : '';
}

export function safeHeaders(sid = null, extra = {}) {
  const headers = {
    Accept: 'application/json',
    'Content-Type': 'application/json',
    'User-Agent': 'aos-k6-load-test/1.0',
    ...extra,
  };
  if (sid) headers.Cookie = `sid=${sid}`;
  return headers;
}

export function getMethod(methodName, params = {}, sid = null, tags = {}) {
  const url = `${methodUrl(methodName)}${queryString(params)}`;
  return http.get(url, {
    headers: safeHeaders(sid),
    timeout: DEFAULT_TIMEOUT,
    tags: { method: methodName, ...tags },
  });
}

export function postMethod(methodName, body = {}, sid = null, tags = {}) {
  return http.post(methodUrl(methodName), JSON.stringify(body || {}), {
    headers: safeHeaders(sid),
    timeout: DEFAULT_TIMEOUT,
    tags: { method: methodName, ...tags },
  });
}

export function unwrapMessage(response) {
  try {
    const json = response.json();
    return json && json.message !== undefined ? json.message : json;
  } catch (_) {
    return null;
  }
}

export function responseCode(response) {
  const message = unwrapMessage(response);
  return message && typeof message === 'object' ? message.code : null;
}

export function responseOk(response) {
  const message = unwrapMessage(response);
  if (!message || typeof message !== 'object') return false;
  return message.ok === true;
}

export function responseData(response) {
  const message = unwrapMessage(response);
  if (!message || typeof message !== 'object') return null;
  return message.data || null;
}

export function record(response, label, options = {}) {
  const allowStatuses = options.allowStatuses || [];
  const allowCodes = options.allowCodes || [];
  const allowRateLimit = options.allowRateLimit !== false;
  const requireBusinessOk = options.requireBusinessOk !== false;

  const statusAllowed =
    response.status >= 200 && response.status < 400 ||
    allowStatuses.includes(response.status) ||
    (allowRateLimit && response.status === 429);

  const code = responseCode(response);
  const codeAllowed = code && allowCodes.includes(code);
  const serverError = response.status >= 500;
  const businessFailed = requireBusinessOk && !responseOk(response) && !codeAllowed && !(allowRateLimit && response.status === 429);

  unexpectedErrors.add(serverError, { endpoint: label });
  businessFailures.add(businessFailed || !statusAllowed, { endpoint: label });

  check(response, {
    [`${label}: no 5xx`]: () => !serverError,
    [`${label}: expected HTTP status`]: () => statusAllowed,
  });

  if (requireBusinessOk && !codeAllowed && !(allowRateLimit && response.status === 429)) {
    check(response, {
      [`${label}: AOS ok response`]: () => responseOk(response),
    });
  }

  return !serverError && statusAllowed && (!requireBusinessOk || responseOk(response) || codeAllowed || (allowRateLimit && response.status === 429));
}

export function login(email = __ENV.USER_EMAIL, password = __ENV.USER_PASSWORD) {
  if (!email || !password) return null;

  const response = postMethod('aos.api.v1.auth.login', { email, password }, null, { flow: 'auth' });
  const ok = record(response, 'auth.login', {
    allowStatuses: [401, 403, 422, 429],
    allowCodes: ['INVALID_CREDENTIALS', 'NOT_VERIFIED', 'VALIDATION_ERROR', 'RATE_LIMITED'],
  });
  if (!ok || !responseOk(response)) return null;

  const data = responseData(response) || {};
  return data.sid || null;
}

export function requireLogin(email = __ENV.USER_EMAIL, password = __ENV.USER_PASSWORD) {
  const sid = login(email, password);
  if (!sid) {
    fail('Authenticated load-test flow requires USER_EMAIL and USER_PASSWORD for an enabled staging test account.');
  }
  return sid;
}

export function loginAdmin() {
  if (!__ENV.ADMIN_EMAIL || !__ENV.ADMIN_PASSWORD) return null;
  return login(__ENV.ADMIN_EMAIL, __ENV.ADMIN_PASSWORD);
}

export function maybeSleep(minSeconds = 0.3, maxSeconds = 1.5) {
  const seconds = minSeconds + Math.random() * (maxSeconds - minSeconds);
  return seconds;
}

export function shortText(prefix = 'k6') {
  return `${prefix} ${Date.now()} ${Math.random().toString(36).slice(2, 8)}`;
}

export function commonThresholds(extra = {}) {
  return {
    http_req_failed: ['rate<0.03'],
    http_req_duration: ['p(95)<1500', 'p(99)<3000'],
    aos_unexpected_errors: ['rate<0.001'],
    aos_business_failures: ['rate<0.05'],
    ...extra,
  };
}
