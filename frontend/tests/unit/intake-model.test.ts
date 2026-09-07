import { expect, test } from 'vitest';
import {
  isGoogleDriveUrl,
  intakeRequest,
  intakeResult,
  loadDraft,
  saveDraft,
  clearUnchangedDraft,
  draftKey,
} from '../../src/features/intake/model.ts';
test('Drive URLs retain the narrow existing allowlist', () => {
  for (const url of [
    'https://drive.google.com/file/d/synthetic-file-123/view',
    'https://drive.google.com/open?id=synthetic-file-123',
    'https://docs.google.com/document/d/synthetic-file-123/edit',
  ])
    expect(isGoogleDriveUrl(url)).toBe(true);
  for (const url of [
    'http://drive.google.com/file/d/synthetic-file-123',
    'https://evil.example/file/d/synthetic-file-123',
    'https://user:secret@drive.google.com/file/d/synthetic-file-123',
    'https://drive.google.com:444/file/d/synthetic-file-123',
    'https://drive.google.com/drive/folders/synthetic-folder-123',
  ])
    expect(isGoogleDriveUrl(url)).toBe(false);
});
test('upload and Drive preserve their distinct payload contracts', () => {
  const upload = new FormData();
  upload.set('kind', 'expense_invoice');
  upload.set('file', new File(['synthetic'], 'proof.txt'));
  upload.set('drive_url', 'not-used');
  const request = intakeRequest(upload, 'upload', '', {
    id: 'synthetic-folder-123',
    name: 'Synthetic',
  });
  expect(request.url).toBe('/api/intake');
  const body = request.options.body as FormData;
  expect(body.get('defer_counterparty')).toBe('1');
  expect(body.get('google_folder_id')).toBe('synthetic-folder-123');
  expect(body.has('drive_url')).toBe(false);
  expect(body.get('file')).toBeInstanceOf(File);
  const drive = new FormData();
  drive.set('kind', 'income_invoice');
  drive.set('file', new File(['unused'], 'unused.txt'));
  drive.set('drive_url', 'https://drive.google.com/file/d/synthetic-file-123/view');
  const json = intakeRequest(
    drive,
    'google_drive',
    ' https://drive.google.com/file/d/synthetic-file-123/view ',
  );
  expect(JSON.parse(String(json.options.body))).toEqual({
    fields: { kind: 'income_invoice' },
    drive_url: 'https://drive.google.com/file/d/synthetic-file-123/view',
  });
});
test('draft schema stays compatible and completion does not delete newer values', () => {
  localStorage.clear();
  saveDraft({ currency: 'EUR' });
  expect(localStorage.getItem(draftKey)).toBeNull();
  saveDraft({ document_number: 'Synthetic first', gross: '0.00' });
  const prior = localStorage.getItem(draftKey);
  expect(loadDraft()).toEqual({ document_number: 'Synthetic first', gross: '0.00' });
  saveDraft({ document_number: 'Synthetic newer' });
  clearUnchangedDraft(prior);
  expect(loadDraft()?.document_number).toBe('Synthetic newer');
  clearUnchangedDraft(localStorage.getItem(draftKey));
  expect(loadDraft()).toBeNull();
  const denied = () => {
    throw new Error('Unavailable');
  };
  expect(() => saveDraft({ currency: 'USD' }, denied)).not.toThrow();
  expect(loadDraft(denied)).toBeNull();
  localStorage.clear();
});

test('malformed or mismatched acknowledgements cannot clear a draft or claim acceptance', () => {
  for (const value of [
    null,
    true,
    {},
    { kind: 'expense_invoice' },
    { kind: 'expense_invoice', period: 'wrong' },
    { kind: 'other', period: '2026-Q3' },
    { kind: 'expense_invoice', period: '2026-Q3' },
    { kind: 'income_invoice', period: '2026-Q2' },
  ])
    expect(() => intakeResult(value, { kind: 'income_invoice', period: '2026-Q3' })).toThrow(
      'intake.invalidResponse',
    );
  expect(
    intakeResult(
      { kind: 'income_invoice', period: '2026-Q3' },
      { kind: 'income_invoice', period: '2026-Q3' },
    ),
  ).toEqual({ kind: 'income_invoice', period: '2026-Q3' });
});

test('unknown draft schemas and malformed values never populate a form', () => {
  for (const raw of [
    '{',
    'null',
    '{"schema":2,"values":{"document_number":"Synthetic"}}',
    '{"schema":1,"values":{"document_number":23}}',
  ]) {
    localStorage.setItem(draftKey, raw);
    expect(loadDraft()).toBeNull();
  }
  localStorage.clear();
});
