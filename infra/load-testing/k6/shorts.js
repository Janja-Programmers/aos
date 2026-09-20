import { group, sleep } from 'k6';
import {
  boolEnv,
  commonThresholds,
  csvEnv,
  getMethod,
  intEnv,
  login,
  postMethod,
  randomItem,
  randomSessionId,
  record,
  shortText,
} from './lib/aos.js';

export const options = {
  vus: intEnv('VUS', 10),
  duration: __ENV.DURATION || '5m',
  thresholds: commonThresholds({
    'http_req_duration{method:aos.api.v1.shorts.feed_for_you}': ['p(95)<1500'],
    'http_req_duration{method:aos.api.v1.shorts.track_view}': ['p(95)<900'],
  }),
};

const SHORT_IDS = csvEnv('SHORT_IDS');
const TEST_SHORT_ID = __ENV.TEST_SHORT_ID || randomItem(SHORT_IDS);

export default function () {
  const sessionId = randomSessionId('shorts');

  group('shorts read/track', () => {
    record(getMethod('aos.api.v1.shorts.feed_for_you', { limit: 10, session_id: sessionId }), 'shorts.feed_for_you');

    const shortId = randomItem(SHORT_IDS) || TEST_SHORT_ID;
    if (shortId) {
      record(getMethod('aos.api.v1.shorts.get_short', { short_id: shortId, session_id: sessionId }), 'shorts.detail', {
        allowStatuses: [404],
        allowCodes: ['NOT_FOUND'],
      });
      record(getMethod('aos.api.v1.shorts.list_comments', { short_id: shortId, limit: 20 }), 'shorts.comments');
      record(postMethod('aos.api.v1.shorts.track_impression', { short_id: shortId, session_id: sessionId }), 'shorts.track_impression', {
        allowStatuses: [404, 422, 429],
        allowCodes: ['NOT_FOUND', 'VALIDATION_ERROR', 'RATE_LIMITED'],
      });
      record(postMethod('aos.api.v1.shorts.track_view', { short_id: shortId, session_id: sessionId, watch_ms: 5000 }), 'shorts.track_view', {
        allowStatuses: [404, 422, 429],
        allowCodes: ['NOT_FOUND', 'VALIDATION_ERROR', 'RATE_LIMITED'],
      });
    }
  });

  if (boolEnv('RUN_SHORT_WRITES', false) || boolEnv('RUN_WRITES', false)) {
    const sid = login();
    const shortId = TEST_SHORT_ID;
    if (sid && shortId) {
      group('shorts authenticated writes', () => {
        record(postMethod('aos.api.v1.shorts.like_short', { short_id: shortId }, sid), 'shorts.like_short', {
          allowStatuses: [404, 422, 429],
          allowCodes: ['NOT_FOUND', 'VALIDATION_ERROR', 'RATE_LIMITED'],
        });
        record(postMethod('aos.api.v1.shorts.add_comment', { short_id: shortId, text: shortText('k6 comment') }, sid), 'shorts.add_comment', {
          allowStatuses: [404, 422, 429],
          allowCodes: ['NOT_FOUND', 'VALIDATION_ERROR', 'RATE_LIMITED'],
        });
      });
    }
  }

  sleep(0.4 + Math.random() * 1.5);
}
