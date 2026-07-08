import { group, sleep } from 'k6';
import {
  boolEnv,
  commonThresholds,
  getMethod,
  intEnv,
  loginAdmin,
  record,
} from './lib/aos.js';

export const options = {
  vus: intEnv('VUS', 1),
  duration: __ENV.DURATION || '1m',
  thresholds: commonThresholds({
    http_req_duration: ['p(95)<2500', 'p(99)<5000'],
  }),
};

export default function () {
  if (!boolEnv('RUN_ADMIN_DIAGNOSTICS', false)) {
    group('admin diagnostics disabled guard', () => {});
    sleep(1);
    return;
  }

  const sid = loginAdmin();
  if (!sid) return;

  group('admin operational diagnostics', () => {
    record(getMethod('aos.api.diagnostics.get_production_config_status', {}, sid), 'diagnostics.production_config');
    record(getMethod('aos.api.diagnostics.get_operational_health_status', {}, sid), 'diagnostics.operational_health');
    record(getMethod('aos.api.diagnostics.get_job_monitoring_status', {}, sid), 'diagnostics.job_monitoring');
    record(getMethod('aos.api.diagnostics.get_backup_readiness_status', {}, sid), 'diagnostics.backup_readiness');
  });

  sleep(3);
}
