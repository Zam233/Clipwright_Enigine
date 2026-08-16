// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { normalizeWebhookTest } from './webhook';

describe('normalizeWebhookTest', () => {
  it('maps backend "sent" status to success with status_code', () => {
    expect(normalizeWebhookTest({ status: 'sent', response_code: 200 })).toEqual({
      success: true,
      status_code: 200,
    });
  });

  it('maps backend "failed" status to success=false with error body', () => {
    expect(normalizeWebhookTest({ status: 'failed', error: 'Connection refused' })).toEqual({
      success: false,
      body: 'Connection refused',
    });
  });

  it('keeps response_code on failed deliveries when present', () => {
    expect(normalizeWebhookTest({ status: 'failed', response_code: 500, error: 'boom' })).toEqual({
      success: false,
      status_code: 500,
      body: 'boom',
    });
  });

  it('tolerates unknown/malformed payloads', () => {
    expect(normalizeWebhookTest(undefined)).toEqual({ success: false });
    expect(normalizeWebhookTest(null)).toEqual({ success: false });
    expect(normalizeWebhookTest({})).toEqual({ success: false });
  });
});
