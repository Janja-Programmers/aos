import { group, sleep } from 'k6';
import {
  boolEnv,
  commonThresholds,
  getMethod,
  intEnv,
  login,
  postMethod,
  record,
  shortText,
} from './lib/aos.js';

export const options = {
  vus: intEnv('VUS', 5),
  duration: __ENV.DURATION || '3m',
  thresholds: commonThresholds({
    'http_req_duration{method:aos.api.v1.chat.list_conversations}': ['p(95)<1200'],
    'http_req_duration{method:aos.api.v1.chat.send_message}': ['p(95)<1500'],
  }),
};

export default function () {
  const sid = login();
  if (!sid) return;

  group('chat read', () => {
    record(getMethod('aos.api.v1.chat.list_conversations', { limit: 20 }, sid), 'chat.list_conversations');

    if (__ENV.CHAT_CONVERSATION_ID) {
      record(getMethod('aos.api.v1.chat.list_messages', {
        conversation_id: __ENV.CHAT_CONVERSATION_ID,
        limit: 30,
      }, sid), 'chat.list_messages', {
        allowStatuses: [403, 404, 422],
        allowCodes: ['FORBIDDEN', 'NOT_FOUND', 'VALIDATION_ERROR'],
      });
    }
  });

  if (boolEnv('RUN_CHAT_WRITES', false) || boolEnv('RUN_WRITES', false)) {
    group('chat writes', () => {
      let conversationId = __ENV.CHAT_CONVERSATION_ID;
      if (!conversationId && __ENV.CHAT_RECEIVER_USER) {
        const openResponse = postMethod('aos.api.v1.chat.open_conversation', { other_user: __ENV.CHAT_RECEIVER_USER }, sid);
        record(openResponse, 'chat.open_conversation', {
          allowStatuses: [403, 404, 422, 429],
          allowCodes: ['FORBIDDEN', 'NOT_FOUND', 'VALIDATION_ERROR', 'RATE_LIMITED'],
        });
        try {
          const data = openResponse.json('message.data');
          conversationId = data && (data.id || data.conversation_id);
        } catch (_) {}
      }

      if (conversationId) {
        record(postMethod('aos.api.v1.chat.send_message', {
          conversation_id: conversationId,
          text: shortText('k6 chat'),
        }, sid), 'chat.send_message', {
          allowStatuses: [403, 404, 422, 429],
          allowCodes: ['FORBIDDEN', 'NOT_FOUND', 'VALIDATION_ERROR', 'RATE_LIMITED'],
        });
      }
    });
  }

  sleep(0.5 + Math.random() * 1.5);
}
