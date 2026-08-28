import { group, sleep } from 'k6';
import {
  boolEnv,
  commonThresholds,
  intEnv,
  login,
  postMethod,
  record,
} from './lib/aos.js';

export const options = {
  vus: intEnv('VUS', 3),
  duration: __ENV.DURATION || '2m',
  thresholds: commonThresholds({
    'http_req_duration{method:aos.api.v1.media.init_upload}': ['p(95)<1200'],
  }),
};

export default function () {
  if (!boolEnv('RUN_MEDIA_INIT', false) && !boolEnv('RUN_WRITES', false)) {
    group('media disabled guard', () => {
      // Keep this script no-op by default because init_upload creates media rows and presigned URLs.
    });
    sleep(1);
    return;
  }

  const sid = login();
  if (!sid) return;

  group('media upload init', () => {
    const purpose = __ENV.MEDIA_PURPOSE || 'ad_image';
    const isShortVideo = purpose === 'short_video_raw';
    const payload = {
      purpose,
      filename: __ENV.MEDIA_FILENAME || `k6-load-${__VU}-${__ITER}-${Date.now()}${isShortVideo ? '.mp4' : '.jpg'}`,
      content_type: __ENV.MEDIA_CONTENT_TYPE || (isShortVideo ? 'video/mp4' : 'image/jpeg'),
      size_bytes: parseInt(__ENV.MEDIA_SIZE_BYTES || '4096', 10),
    };
    if (__ENV.MEDIA_DURATION_SECONDS || isShortVideo) {
      payload.duration_seconds = parseFloat(__ENV.MEDIA_DURATION_SECONDS || '30');
    }
    record(postMethod('aos.api.v1.media.init_upload', payload, sid), 'media.init_upload', {
      allowStatuses: [422, 429],
      allowCodes: ['VALIDATION_ERROR', 'RATE_LIMITED'],
    });
  });

  sleep(0.5 + Math.random());
}
