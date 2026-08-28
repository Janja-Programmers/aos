import http from 'k6/http';
import { check, group, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import {
  boolEnv,
  commonThresholds,
  csvEnv,
  getMethod,
  intEnv,
  login,
  postMethod,
  record,
  responseData,
} from './lib/aos.js';

const VIDEO_PATH = (__ENV.SHORT_VIDEO_FILE || '').trim();
const VIDEO_BYTES = VIDEO_PATH ? open(VIDEO_PATH, 'b') : null;
const VIDEO_DURATION_SECONDS = parseFloat(__ENV.SHORT_VIDEO_DURATION_SECONDS || '0');
const VIDEO_CONTENT_TYPE = __ENV.SHORT_VIDEO_CONTENT_TYPE || 'video/mp4';
const WAIT_FOR_PROCESSING = boolEnv('WAIT_FOR_PROCESSING', false);
const TARGET_UPLOADS_PER_MINUTE = intEnv('TARGET_UPLOADS_PER_MINUTE', 0);
const PROCESSING_UTILIZATION = Math.min(
  0.95,
  Math.max(0.1, parseFloat(__ENV.PROCESSING_TARGET_UTILIZATION || '0.70')),
);
const USER_EMAIL_POOL = csvEnv('SHORT_UPLOAD_USER_EMAILS');
const USER_PASSWORD_POOL = csvEnv('SHORT_UPLOAD_USER_PASSWORDS');
const SID_POOL = csvEnv('SHORT_UPLOAD_SIDS');
const ARRIVAL_RATE_UPLOADS_PER_MINUTE = intEnv('ARRIVAL_RATE_UPLOADS_PER_MINUTE', 0);

const uploadCompleted = new Counter('short_upload_completed');
const multipartPartsUploaded = new Counter('short_multipart_parts_uploaded');
const processingReady = new Counter('short_processing_ready');
const processingFailed = new Counter('short_processing_failed');
const processingSeconds = new Trend('short_processing_seconds');

const thresholds = commonThresholds({
    // Heavy object-store PUTs are intentionally excluded from normal API SLOs.
    http_req_duration: ['p(95)<120000', 'p(99)<300000'],
    'http_req_duration{flow:short_upload_put}': ['p(95)<120000'],
    'http_req_duration{flow:short_upload_part}': ['p(95)<120000'],
    'http_req_duration{method:aos.api.v1.media.multipart_part_urls}': ['p(95)<1500'],
    'http_req_duration{method:aos.api.v1.media.multipart_status}': ['p(95)<1500'],
    'http_req_duration{method:aos.api.v1.media.complete_multipart_upload}': ['p(95)<3000'],
    'http_req_duration{method:aos.api.v1.media.confirm_upload}': ['p(95)<1500'],
  });

export const options = ARRIVAL_RATE_UPLOADS_PER_MINUTE > 0
  ? {
      scenarios: {
        short_upload_arrival: {
          executor: 'constant-arrival-rate',
          rate: ARRIVAL_RATE_UPLOADS_PER_MINUTE,
          timeUnit: '1m',
          duration: __ENV.DURATION || '5m',
          preAllocatedVUs: intEnv('PREALLOCATED_VUS', Math.max(10, intEnv('VUS', 10))),
          maxVUs: intEnv('MAX_VUS', Math.max(50, intEnv('VUS', 10) * 5)),
          gracefulStop: __ENV.GRACEFUL_STOP || '30s',
        },
      },
      thresholds,
    }
  : {
      vus: intEnv('VUS', 2),
      iterations: intEnv('ITERATIONS', 2),
      thresholds,
    };

function putObject(url, bytes, headers) {
  const response = http.put(url, bytes, {
    headers: headers || { 'Content-Type': VIDEO_CONTENT_TYPE },
    timeout: __ENV.UPLOAD_TIMEOUT || '20m',
    tags: { flow: 'short_upload_put' },
  });
  return check(response, {
    'short upload PUT accepted': (r) => r.status >= 200 && r.status < 300,
  });
}

function loginUploadUser() {
  if (SID_POOL.length > 0) {
    return SID_POOL[(__VU - 1) % SID_POOL.length];
  }
  if (USER_EMAIL_POOL.length === 0) return login();
  const index = (__VU - 1) % USER_EMAIL_POOL.length;
  const email = USER_EMAIL_POOL[index];
  const password = USER_PASSWORD_POOL.length === 1
    ? USER_PASSWORD_POOL[0]
    : USER_PASSWORD_POOL[index];
  if (!password) {
    check(null, { 'Short upload credential pool has a password for each user': () => false });
    return null;
  }
  return login(email, password);
}

function multipartStatus(mediaId, sid) {
  const response = postMethod(
    'aos.api.v1.media.multipart_status',
    { media_id: mediaId },
    sid,
    { flow: 'short_upload_resume_status' },
  );
  if (!record(response, 'short_upload.multipart_status', {
    allowStatuses: [409, 410, 429],
    allowCodes: ['INVALID_STATE', 'MULTIPART_SESSION_LOST', 'UPLOAD_EXPIRED', 'RATE_LIMITED'],
  })) return null;
  return responseData(response);
}

function uploadMultipart(init, sid) {
  const mediaId = init.media_id;
  const contract = init.multipart || {};
  const partSize = Number(contract.part_size_bytes || 0);
  const partCount = Number(contract.part_count || 0);
  const serverParallel = Number(contract.max_parallel_parts || 1);
  const requestedParallel = Math.max(1, intEnv('SHORT_UPLOAD_PART_PARALLELISM', serverParallel));
  const parallel = Math.max(1, Math.min(serverParallel || 1, requestedParallel));

  if (!mediaId || partSize <= 0 || partCount <= 0) {
    check(init, { 'multipart init returned a valid contract': () => false });
    return false;
  }

  // Always begin from authoritative storage state. This makes the same client
  // algorithm correct for a fresh upload and for a resumed upload.
  let status = multipartStatus(mediaId, sid);
  if (!status) return false;
  let missing = Array.isArray(status.retry_parts)
    ? status.retry_parts.slice()
    : [
        ...(Array.isArray(status.missing_parts) ? status.missing_parts : []),
        ...(Array.isArray(status.invalid_parts) ? status.invalid_parts : []),
      ].filter((value, index, values) => values.indexOf(value) === index);
  let cycles = 0;
  const maxCycles = Math.max(partCount * 3, 12);

  while (missing.length > 0 && cycles < maxCycles) {
    cycles += 1;
    const firstMissing = Number(missing[0]);
    const requestCount = Math.min(
      Math.max(parallel, Number(status.part_url_batch_size || parallel)),
      partCount - firstMissing + 1,
    );
    const urlResponse = postMethod(
      'aos.api.v1.media.multipart_part_urls',
      { media_id: mediaId, start_part: firstMissing, count: requestCount },
      sid,
      { flow: 'short_upload_part_urls' },
    );
    if (!record(urlResponse, 'short_upload.multipart_part_urls', {
      allowStatuses: [409, 410, 422, 429],
      allowCodes: ['INVALID_STATE', 'MULTIPART_SESSION_LOST', 'UPLOAD_EXPIRED', 'VALIDATION_ERROR', 'RATE_LIMITED'],
    })) return false;

    const urlData = responseData(urlResponse) || {};
    const missingSet = new Set(missing.map((value) => Number(value)));
    const candidateParts = (urlData.parts || [])
      .filter((part) => missingSet.has(Number(part.part_number)))
      .slice(0, parallel);
    if (candidateParts.length === 0) {
      check(urlData, { 'multipart URL batch contains a missing part': () => false });
      return false;
    }

    const requests = candidateParts.map((part) => {
      const partNumber = Number(part.part_number);
      const expectedSize = Number(part.expected_size_bytes);
      const offset = (partNumber - 1) * partSize;
      const body = VIDEO_BYTES.slice(offset, offset + expectedSize);
      return {
        method: 'PUT',
        url: part.upload_url,
        body,
        params: {
          headers: { 'Content-Type': 'application/octet-stream' },
          timeout: __ENV.UPLOAD_TIMEOUT || '20m',
          tags: { flow: 'short_upload_part' },
        },
      };
    });

    const responses = http.batch(requests);
    let batchOk = true;
    responses.forEach((response) => {
      const ok = check(response, {
        'multipart UploadPart accepted': (r) => r.status >= 200 && r.status < 300,
      });
      if (ok) multipartPartsUploaded.add(1);
      batchOk = batchOk && ok;
    });

    // Do not guess successful parts from local HTTP state. Re-list storage on
    // every batch boundary so retries remain correct after ambiguous failures.
    status = multipartStatus(mediaId, sid);
    if (!status) return false;
    missing = Array.isArray(status.retry_parts)
      ? status.retry_parts.slice()
      : [
          ...(Array.isArray(status.missing_parts) ? status.missing_parts : []),
          ...(Array.isArray(status.invalid_parts) ? status.invalid_parts : []),
        ].filter((value, index, values) => values.indexOf(value) === index);
    if (!batchOk) sleep(0.5 + Math.random());
  }

  if (missing.length > 0) {
    check(status, { 'multipart upload converged before retry budget': () => false });
    return false;
  }

  const completeResponse = postMethod(
    'aos.api.v1.media.complete_multipart_upload',
    { media_id: mediaId },
    sid,
    { flow: 'short_upload_complete' },
  );
  return record(completeResponse, 'short_upload.complete_multipart_upload', {
    allowStatuses: [409, 410, 422, 429],
    allowCodes: [
      'INVALID_STATE',
      'MULTIPART_INCOMPLETE',
      'MULTIPART_PART_SIZE_MISMATCH',
      'MULTIPART_SESSION_LOST',
      'UPLOAD_EXPIRED',
      'VALIDATION_ERROR',
      'RATE_LIMITED',
    ],
  });
}

function waitForShortProcessing(shortId, sid) {
  if (!WAIT_FOR_PROCESSING || !shortId) return;
  const startedAt = Date.now();
  const timeoutSeconds = Math.max(60, intEnv('PROCESSING_WAIT_TIMEOUT_SECONDS', 7200));
  const pollSeconds = Math.max(2, intEnv('PROCESSING_POLL_SECONDS', 10));

  while ((Date.now() - startedAt) / 1000 < timeoutSeconds) {
    const response = getMethod(
      'aos.api.v1.shorts.get_short',
      { short_id: shortId },
      sid,
      { flow: 'short_processing_poll' },
    );
    if (!record(response, 'short_upload.processing_status', {
      allowStatuses: [404, 409, 429],
      allowCodes: ['NOT_FOUND', 'RATE_LIMITED'],
    })) return;

    const data = responseData(response) || {};
    const item = data.item || {};
    const state = String(item.status || '').toLowerCase();
    if (state === 'ready') {
      const elapsed = (Date.now() - startedAt) / 1000;
      processingSeconds.add(elapsed);
      processingReady.add(1);
      return;
    }
    if (state === 'failed') {
      const elapsed = (Date.now() - startedAt) / 1000;
      processingSeconds.add(elapsed);
      processingFailed.add(1);
      check(item, { 'short processing did not fail': () => false });
      return;
    }
    sleep(pollSeconds);
  }

  processingFailed.add(1);
  check(null, { 'short processing completed before timeout': () => false });
}

export default function () {
  if (!VIDEO_BYTES || !VIDEO_PATH || !Number.isFinite(VIDEO_DURATION_SECONDS) || VIDEO_DURATION_SECONDS <= 0) {
    check(null, {
      'SHORT_VIDEO_FILE is configured': () => Boolean(VIDEO_BYTES && VIDEO_PATH),
      'SHORT_VIDEO_DURATION_SECONDS is configured': () => Number.isFinite(VIDEO_DURATION_SECONDS) && VIDEO_DURATION_SECONDS > 0,
    });
    sleep(1);
    return;
  }

  const sid = loginUploadUser();
  if (!sid) return;

  group('short upload end-to-end', () => {
    const filename = VIDEO_PATH.split('/').pop() || `k6-short-${__VU}-${__ITER}.mp4`;
    const initResponse = postMethod('aos.api.v1.media.init_upload', {
      purpose: 'short_video_raw',
      filename,
      content_type: VIDEO_CONTENT_TYPE,
      size_bytes: VIDEO_BYTES.byteLength,
      duration_seconds: VIDEO_DURATION_SECONDS,
      upload_mode: 'auto',
      idempotency_key: `k6-${__VU}-${__ITER}-${Date.now()}`,
    }, sid, { flow: 'short_upload_init' });
    if (!record(initResponse, 'short_upload.init', {
      allowStatuses: [413, 422, 429],
      allowCodes: ['FILE_TOO_LARGE', 'DURATION_REQUIRED', 'MULTIPART_ACTIVE_LIMIT', 'VALIDATION_ERROR', 'RATE_LIMITED'],
    })) return;

    const init = responseData(initResponse) || {};
    if (!init.media_id || !init.upload_mode) {
      check(init, { 'short upload init returned media id and upload mode': () => false });
      return;
    }

    let uploaded = false;
    if (init.upload_mode === 'multipart') {
      uploaded = uploadMultipart(init, sid);
    } else {
      if (!init.upload_url) {
        check(init, { 'direct upload init returned URL': () => false });
        return;
      }
      const uploadHeaders = { ...(init.upload_headers || {}) };
      if (!uploadHeaders['Content-Type']) uploadHeaders['Content-Type'] = VIDEO_CONTENT_TYPE;
      if (!putObject(init.upload_url, VIDEO_BYTES, uploadHeaders)) return;

      const confirmResponse = postMethod(
        'aos.api.v1.media.confirm_upload',
        { media_id: init.media_id },
        sid,
        { flow: 'short_upload_confirm' },
      );
      uploaded = record(confirmResponse, 'short_upload.confirm', {
        allowStatuses: [409, 410, 422, 429],
        allowCodes: ['INVALID_STATE', 'UPLOAD_EXPIRED', 'VALIDATION_ERROR', 'RATE_LIMITED'],
      });
    }
    if (!uploaded) return;
    uploadCompleted.add(1);

    if (boolEnv('RUN_SHORT_CREATE', false)) {
      const createResponse = postMethod('aos.api.v1.shorts.create_short', {
        raw_video_media: init.media_id,
        caption: __ENV.SHORT_CAPTION || 'k6 short upload processing test',
      }, sid, { flow: 'short_upload_create' });
      if (!record(createResponse, 'short_upload.create_short', {
        allowStatuses: [409, 422, 429],
        allowCodes: ['VALIDATION_ERROR', 'RATE_LIMITED'],
      })) return;
      const createData = responseData(createResponse) || {};
      waitForShortProcessing(createData.short_id, sid);
    }
  });

  sleep(0.5 + Math.random());
}

function summaryMetric(data, name, key, fallback = 0) {
  const metric = data.metrics[name];
  if (!metric || !metric.values || metric.values[key] === undefined) return fallback;
  return Number(metric.values[key]);
}

export function handleSummary(data) {
  const avgProcessing = summaryMetric(data, 'short_processing_seconds', 'avg', 0);
  const p95Processing = summaryMetric(data, 'short_processing_seconds', 'p(95)', avgProcessing);
  const runSeconds = Math.max(
    0.001,
    Number((data.state && data.state.testRunDurationMs) || 0) / 1000,
  );
  const completedUploads = summaryMetric(data, 'short_upload_completed', 'count', 0);
  const achievedUploadsPerMinute = completedUploads * 60 / runSeconds;
  const droppedIterations = summaryMetric(data, 'dropped_iterations', 'count', 0);
  const lines = [
    '',
    'AOS heavy Short upload summary',
    `  iterations: ${summaryMetric(data, 'iterations', 'count', 0)}`,
    `  completed media uploads: ${completedUploads}`,
    `  achieved completed uploads/min: ${achievedUploadsPerMinute.toFixed(2)}`,
    `  dropped arrival iterations: ${droppedIterations}`,
    `  multipart parts uploaded: ${summaryMetric(data, 'short_multipart_parts_uploaded', 'count', 0)}`,
    `  processing ready: ${summaryMetric(data, 'short_processing_ready', 'count', 0)}`,
    `  processing failed/timeout: ${summaryMetric(data, 'short_processing_failed', 'count', 0)}`,
    `  HTTP p95: ${summaryMetric(data, 'http_req_duration', 'p(95)', 0).toFixed(2)} ms`,
  ];
  if (avgProcessing > 0) {
    lines.push(`  create-to-ready avg: ${avgProcessing.toFixed(2)} s`);
    lines.push(`  create-to-ready p95: ${p95Processing.toFixed(2)} s`);
  }

  if (TARGET_UPLOADS_PER_MINUTE > 0 && avgProcessing > 0) {
    const avgWorkers = Math.ceil(
      (TARGET_UPLOADS_PER_MINUTE * avgProcessing) / (60 * PROCESSING_UTILIZATION),
    );
    const p95Workers = Math.ceil(
      (TARGET_UPLOADS_PER_MINUTE * p95Processing) / (60 * PROCESSING_UTILIZATION),
    );
    lines.push('');
    lines.push('AOS Shorts video-worker sizing estimate');
    lines.push(`  target uploads/min: ${TARGET_UPLOADS_PER_MINUTE}`);
    lines.push(`  target worker utilization: ${(PROCESSING_UTILIZATION * 100).toFixed(0)}%`);
    lines.push(`  avg-latency estimate: >= ${avgWorkers} video workers`);
    lines.push(`  p95-latency estimate: >= ${p95Workers} video workers (conservative)`);
    lines.push('  Calibrate at low concurrency so queue wait is near zero before using this as service-time sizing.');
    lines.push('  Then validate queue age, CPU/RAM, object-store/network and callback headroom under target arrival load.');
  } else if (TARGET_UPLOADS_PER_MINUTE > 0) {
    lines.push('');
    lines.push('Worker sizing unavailable: enable RUN_SHORT_CREATE=true and WAIT_FOR_PROCESSING=true.');
  }
  if (ARRIVAL_RATE_UPLOADS_PER_MINUTE > 0) {
    lines.push('');
    lines.push('AOS upload arrival-rate rehearsal');
    lines.push(`  configured arrivals/min: ${ARRIVAL_RATE_UPLOADS_PER_MINUTE}`);
    lines.push(`  achieved completed uploads/min: ${achievedUploadsPerMinute.toFixed(2)}`);
    if (droppedIterations > 0) {
      lines.push('  WARNING: k6 dropped iterations; raise MAX_VUS or reduce arrival rate before interpreting backend saturation.');
    }
  }
  lines.push('');
  return { stdout: `${lines.join('\n')}\n` };
}
