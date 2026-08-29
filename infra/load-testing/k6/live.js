import { group, sleep } from 'k6';
import {
  boolEnv,
  commonThresholds,
  getMethod,
  intEnv,
  login,
  postMethod,
  randomSessionId,
  record,
  responseData,
} from './lib/aos.js';

export const options = {
  vus: intEnv('VUS', 5),
  duration: __ENV.DURATION || '3m',
  thresholds: commonThresholds({
    'http_req_duration{method:aos.api.v1.live.join_live}': ['p(95)<1500'],
    'http_req_duration{method:aos.api.v1.live.get_live_token}': ['p(95)<1500'],
    'http_req_duration{method:aos.api.v1.live.track_join}': ['p(95)<1200'],
    'http_req_duration{method:aos.api.v1.live.send_reaction}': ['p(95)<1000'],
    'http_req_duration{method:aos.api.v1.live.add_live_message}': ['p(95)<1200'],
  }),
};

export default function () {
  const sessionId = randomSessionId('live');
  let liveId = __ENV.LIVE_ID || null;
  let sid = null;

  group('live public browse', () => {
    record(getMethod('aos.api.v1.live.list_live_streams', { limit: 20 }), 'live.list_live_streams');
    if (liveId) {
      record(getMethod('aos.api.v1.live.get_live', { live_id: liveId }), 'live.get_live', {
        allowStatuses: [404],
        allowCodes: ['NOT_FOUND'],
      });
      record(getMethod('aos.api.v1.live.list_live_messages', { live_id: liveId, limit: 30 }), 'live.list_messages', {
        allowStatuses: [404],
        allowCodes: ['NOT_FOUND'],
      });
    }
  });

  if (boolEnv('RUN_LIVE_START', false) || boolEnv('RUN_WRITES', false)) {
    sid = login();
    if (sid) {
      const startResponse = postMethod('aos.api.v1.live.start_live', {
        title: `k6 live ${Date.now()}`,
      }, sid);
      record(startResponse, 'live.start_live', {
        allowStatuses: [403, 422, 429],
        allowCodes: ['PERMISSION_DENIED', 'VALIDATION_ERROR', 'RATE_LIMITED'],
      });
      const data = responseData(startResponse) || {};
      const live = data.live || {};
      liveId = live.id || live.name || data.live_id || liveId;
    }
  }

  if ((boolEnv('RUN_LIVE_JOIN', false) || boolEnv('RUN_WRITES', false)) && liveId) {
    sid = sid || login();
    group('live join/token/tracking', () => {
      record(postMethod('aos.api.v1.live.join_live', { live_id: liveId, session_id: sessionId }, sid), 'live.join_live', {
        allowStatuses: [404, 422, 429],
        allowCodes: ['NOT_FOUND', 'VALIDATION_ERROR', 'RATE_LIMITED'],
      });
      record(postMethod('aos.api.v1.live.get_live_token', { live_id: liveId, session_id: sessionId }, sid), 'live.get_live_token', {
        allowStatuses: [404, 422, 429],
        allowCodes: ['NOT_FOUND', 'VALIDATION_ERROR', 'RATE_LIMITED'],
      });
      record(postMethod('aos.api.v1.live.track_join', { live_id: liveId, session_id: sessionId }, sid), 'live.track_join', {
        allowStatuses: [404, 422, 429],
        allowCodes: ['NOT_FOUND', 'VALIDATION_ERROR', 'RATE_LIMITED'],
      });
    });

    if (boolEnv('RUN_LIVE_ENGAGEMENT', false) || boolEnv('RUN_WRITES', false)) {
      group('live engagement storm', () => {
        const reactionBurst = Math.max(1, intEnv('LIVE_REACTION_BURST', 3));
        for (let i = 0; i < reactionBurst; i += 1) {
          record(postMethod('aos.api.v1.live.send_reaction', {
            live_id: liveId,
            session_id: sessionId,
            reaction_type: ['like', 'love', 'fire', 'clap'][i % 4],
          }, sid), 'live.send_reaction', {
            allowStatuses: [404, 422, 429],
            allowCodes: ['NOT_FOUND', 'VALIDATION_ERROR', 'RATE_LIMITED'],
          });
        }
        if (boolEnv('RUN_LIVE_COMMENTS', false)) {
          record(postMethod('aos.api.v1.live.add_live_message', {
            live_id: liveId,
            session_id: sessionId,
            content: `k6 comment ${__VU}-${__ITER}`,
            idempotency_key: `k6-${sessionId}-${__ITER}`,
          }, sid), 'live.add_live_message', {
            allowStatuses: [404, 422, 429],
            allowCodes: ['NOT_FOUND', 'VALIDATION_ERROR', 'RATE_LIMITED'],
          });
        }
      });
    }

    if (boolEnv('RUN_LIVE_LEAVE', false)) {
      record(postMethod('aos.api.v1.live.track_leave', { live_id: liveId, session_id: sessionId }, sid), 'live.track_leave', {
        allowStatuses: [404, 422, 429],
        allowCodes: ['NOT_FOUND', 'VALIDATION_ERROR', 'RATE_LIMITED'],
      });
    }
  }

  sleep(0.2 + Math.random() * 0.8);
}
