import { group, sleep } from 'k6';
import {
  commonThresholds,
  csvEnv,
  getMethod,
  intEnv,
  randomItem,
  record,
} from './lib/aos.js';

export const options = {
  vus: intEnv('VUS', 10),
  duration: __ENV.DURATION || '5m',
  thresholds: commonThresholds({
    'http_req_duration{method:aos.api.v1.ads.list_ads}': ['p(95)<1200'],
    'http_req_duration{method:aos.api.v1.ads.get_ad}': ['p(95)<900'],
  }),
};

const AD_IDS = csvEnv('AD_IDS');
const SEARCH_TERMS = csvEnv('AD_SEARCH_TERMS').length ? csvEnv('AD_SEARCH_TERMS') : ['phone', 'laptop', 'shoes', 'service'];

export default function () {
  group('ads browse/search', () => {
    record(getMethod('aos.api.v1.ads.list_ads', { limit: 20, sort: 'recent' }), 'ads.list.recent');
    record(getMethod('aos.api.v1.ads.list_ads', { limit: 20, sort: 'price_low' }), 'ads.list.price_low', { allowCodes: ['VALIDATION_ERROR'] });
    record(getMethod('aos.api.v1.ads.list_ads', { limit: 20, q: randomItem(SEARCH_TERMS) }), 'ads.search');

    const adId = randomItem(AD_IDS);
    if (adId) {
      record(getMethod('aos.api.v1.ads.get_ad', { ad_id: adId }), 'ads.detail', { allowStatuses: [404], allowCodes: ['NOT_FOUND'] });
    }
  });

  sleep(0.3 + Math.random() * 1.2);
}
