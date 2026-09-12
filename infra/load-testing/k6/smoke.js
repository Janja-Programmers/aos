import { group, sleep } from 'k6';
import {
  boolEnv,
  commonThresholds,
  getMethod,
  intEnv,
  login,
  postMethod,
  record,
  responseOk,
} from './lib/aos.js';

export const options = {
  vus: intEnv('VUS', 1),
  iterations: intEnv('ITERATIONS', 1),
  thresholds: commonThresholds({
    http_req_duration: ['p(95)<1200', 'p(99)<2500'],
  }),
};

export default function () {
  let sid = null;

  group('public read smoke', () => {
    record(getMethod('aos.api.v1.ads.list_ads', { limit: 10, sort: 'recent' }), 'ads.list');
    record(getMethod('aos.api.v1.shorts.feed_for_you', { limit: 10, session_id: `k6-smoke-${__VU}-${__ITER}` }), 'shorts.feed_for_you');
    record(getMethod('aos.api.v1.live.list_live_streams', { limit: 10 }), 'live.list');
    record(getMethod('aos.api.v1.maps.autocomplete_places', { q: 'Tokyo', limit: 5 }), 'maps.autocomplete');
    record(getMethod('aos.api.v1.maps.search_places', { q: 'Tokyo', limit: 5 }), 'maps.search');
    record(getMethod('aos.api.v1.maps.reverse_geocode', { lat: -1.286389, lon: 36.817223 }), 'maps.reverse');
  });

  group('auth/session smoke', () => {
    sid = login();
    if (sid) {
      record(getMethod('aos.api.v1.auth.me', {}, sid), 'auth.me');
      record(postMethod('aos.api.v1.auth.logout', {}, sid), 'auth.logout', { allowStatuses: [200] });
    }
  });

  if (boolEnv('RUN_MEDIA_INIT', false) && sid) {
    group('media upload init smoke', () => {
      const response = postMethod('aos.api.v1.media.init_upload', {
        purpose: 'ad_image',
        filename: `k6-smoke-${Date.now()}.jpg`,
        content_type: 'image/jpeg',
        size_bytes: 2048,
      }, sid);
      record(response, 'media.init_upload');
      if (!responseOk(response)) sleep(1);
    });
  }
}
