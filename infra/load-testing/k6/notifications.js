import { group, sleep } from 'k6';
import {
  boolEnv,
  commonThresholds,
  getMethod,
  intEnv,
  login,
  postMethod,
  record,
} from './lib/aos.js';

export const options = {
  vus: intEnv('VUS', 3),
  duration: __ENV.DURATION || '2m',
  thresholds: commonThresholds(),
};

export default function () {
  const sid = login();
  if (!sid) return;

  group('notifications read', () => {
    record(getMethod('aos.api.v1.notifications.list_notifications', { limit: 20 }, sid), 'notifications.list');
  });

  if (boolEnv('RUN_NOTIFICATION_WRITES', false) || boolEnv('RUN_WRITES', false)) {
    group('push token register/deactivate', () => {
      const token = `k6-token-${__VU}-${__ITER}-${Date.now()}`;
      record(postMethod('aos.api.v1.notifications.register_push_token', {
        token,
        platform: __ENV.PUSH_PLATFORM || 'android',
        device_id: `k6-device-${__VU}`,
      }, sid), 'notifications.register_push_token', {
        allowStatuses: [422, 429],
        allowCodes: ['VALIDATION_ERROR', 'RATE_LIMITED'],
      });
      record(postMethod('aos.api.v1.notifications.deactivate_push_token', {
        token,
        device_id: `k6-device-${__VU}`,
      }, sid), 'notifications.deactivate_push_token', {
        allowStatuses: [422, 429],
        allowCodes: ['VALIDATION_ERROR', 'RATE_LIMITED'],
      });
    });
  }

  sleep(0.5 + Math.random());
}
