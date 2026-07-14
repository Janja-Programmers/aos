import { group, sleep } from 'k6';
import {
  boolEnv,
  commonThresholds,
  csvEnv,
  getMethod,
  intEnv,
  login,
  loginAdmin,
  postMethod,
  randomItem,
  randomSessionId,
  record,
  shortText,
} from './lib/aos.js';

const vus = intEnv('VUS', 20);
const duration = __ENV.DURATION || '10m';

export const options = {
  scenarios: {
    public_browse: {
      executor: 'constant-vus',
      vus: Math.max(1, Math.floor(vus * 0.45)),
      duration,
      exec: 'publicBrowse',
    },
    shorts_activity: {
      executor: 'constant-vus',
      vus: Math.max(1, Math.floor(vus * 0.25)),
      duration,
      exec: 'shortsActivity',
    },
    authenticated_light: {
      executor: 'constant-vus',
      vus: Math.max(1, Math.floor(vus * 0.20)),
      duration,
      exec: 'authenticatedLight',
    },
    low_rate_diagnostics: {
      executor: 'constant-vus',
      vus: 1,
      duration,
      exec: 'diagnosticsLight',
    },
  },
  thresholds: commonThresholds({
    http_req_failed: ['rate<0.03'],
    http_req_duration: ['p(95)<1800', 'p(99)<4000'],
    'http_req_duration{method:aos.api.v1.ads.list_ads}': ['p(95)<1500'],
    'http_req_duration{method:aos.api.v1.shorts.feed_for_you}': ['p(95)<1800'],
    'http_req_duration{method:aos.api.v1.shorts.track_view}': ['p(95)<1200'],
  }),
};

const AD_IDS = csvEnv('AD_IDS');
const SHORT_IDS = csvEnv('SHORT_IDS');
const TEST_SHORT_ID = __ENV.TEST_SHORT_ID || randomItem(SHORT_IDS);

export function publicBrowse() {
  group('public mixed browse', () => {
    record(getMethod('aos.api.v1.ads.list_ads', { limit: 20, sort: 'recent' }), 'ads.list.recent');
    record(getMethod('aos.api.v1.ads.list_ads', { limit: 20, q: randomItem(['phone', 'laptop', 'fashion', 'service']) }), 'ads.search');
    const adId = randomItem(AD_IDS);
    if (adId) record(getMethod('aos.api.v1.ads.get_ad', { ad_id: adId }), 'ads.detail', { allowStatuses: [404], allowCodes: ['NOT_FOUND'] });
    record(getMethod('aos.api.v1.maps.autocomplete_places', { q: 'Nairobi', limit: 5 }), 'maps.autocomplete');
    record(getMethod('aos.api.v1.live.list_live_streams', { limit: 10 }), 'live.list');
  });
  sleep(0.4 + Math.random() * 1.4);
}

export function shortsActivity() {
  const sessionId = randomSessionId('mixed-short');
  const shortId = randomItem(SHORT_IDS) || TEST_SHORT_ID;
  group('shorts mixed activity', () => {
    record(getMethod('aos.api.v1.shorts.feed_for_you', { limit: 10, session_id: sessionId }), 'shorts.feed_for_you');
    if (shortId) {
      record(postMethod('aos.api.v1.shorts.track_view', { short_id: shortId, session_id: sessionId, watch_ms: 5000 }), 'shorts.track_view', {
        allowStatuses: [404, 422, 429],
        allowCodes: ['NOT_FOUND', 'VALIDATION_ERROR', 'RATE_LIMITED'],
      });
      record(getMethod('aos.api.v1.shorts.list_comments', { short_id: shortId, limit: 20 }), 'shorts.comments');
    }
  });
  sleep(0.5 + Math.random() * 1.5);
}

export function authenticatedLight() {
  const sid = login();
  if (!sid) {
    sleep(2);
    return;
  }

  group('authenticated light mixed', () => {
    record(getMethod('aos.api.v1.auth.me', {}, sid), 'auth.me');
    record(getMethod('aos.api.v1.notifications.list_notifications', { limit: 20 }, sid), 'notifications.list');
    record(getMethod('aos.api.v1.chat.list_conversations', { limit: 20 }, sid), 'chat.list_conversations');

    if ((boolEnv('RUN_SHORT_WRITES', false) || boolEnv('RUN_WRITES', false)) && TEST_SHORT_ID) {
      record(postMethod('aos.api.v1.shorts.add_comment', { short_id: TEST_SHORT_ID, text: shortText('k6 mixed') }, sid), 'shorts.add_comment', {
        allowStatuses: [404, 422, 429],
        allowCodes: ['NOT_FOUND', 'VALIDATION_ERROR', 'RATE_LIMITED'],
      });
    }
  });
  sleep(0.7 + Math.random() * 2);
}

export function diagnosticsLight() {
  if (!boolEnv('RUN_ADMIN_DIAGNOSTICS', false)) {
    sleep(10);
    return;
  }
  const sid = loginAdmin();
  if (!sid) {
    sleep(10);
    return;
  }
  group('low-rate diagnostics', () => {
    record(getMethod('aos.api.v1.diagnostics.get_operational_health_status', {}, sid), 'diagnostics.operational_health');
    record(getMethod('aos.api.v1.diagnostics.get_job_monitoring_status', {}, sid), 'diagnostics.job_monitoring');
  });
  sleep(30);
}
