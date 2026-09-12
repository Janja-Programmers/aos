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
  vus: intEnv('VUS', 5),
  duration: __ENV.DURATION || '3m',
  thresholds: commonThresholds({
    'http_req_duration{method:aos.api.v1.maps.autocomplete_places}': ['p(95)<1200'],
    'http_req_duration{method:aos.api.v1.maps.search_places}': ['p(95)<1500'],
  }),
};

export default function () {
  group('maps public location endpoints', () => {
    record(getMethod('aos.api.v1.maps.autocomplete_places', { q: __ENV.MAP_QUERY || 'Tokyo', limit: 5 }), 'maps.autocomplete');
    record(getMethod('aos.api.v1.maps.search_places', { q: __ENV.MAP_QUERY || 'Tokyo', limit: 10 }), 'maps.search');
    record(getMethod('aos.api.v1.maps.reverse_geocode', {
      lat: __ENV.MAP_LAT || -1.286389,
      lon: __ENV.MAP_LON || 36.817223,
    }), 'maps.reverse');
  });

  if (boolEnv('RUN_MAP_ROUTE', false) || boolEnv('RUN_WRITES', false)) {
    const sid = login();
    if (sid) {
      group('maps route endpoint', () => {
        record(postMethod('aos.api.v1.maps.get_route', {
          points: [
            { lat: -1.286389, lon: 36.817223 },
            { lat: -1.292066, lon: 36.821946 },
          ],
        }, sid), 'maps.get_route', {
          allowStatuses: [403, 422, 429],
          allowCodes: ['FORBIDDEN', 'VALIDATION_ERROR', 'RATE_LIMITED'],
        });
      });
    }
  }

  sleep(0.4 + Math.random());
}
