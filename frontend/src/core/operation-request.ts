interface OperationRequest {
  preview_token: string;
  expected_version: number;
  request_id: string;
}
const pending = new Map<string, OperationRequest>();
/** Preserve the idempotency key even when browser storage is disabled. */
export function operationRequest(
  key: string,
  token: string,
  version: number,
  storage: () => Pick<Storage, 'getItem' | 'setItem'> = () => sessionStorage,
  uuid: () => string = () => crypto.randomUUID(),
): OperationRequest {
  const matches = (value: unknown): value is OperationRequest => {
    const row = value as OperationRequest | null;
    return (
      !!row &&
      row.preview_token === token &&
      row.expected_version === version &&
      typeof row.request_id === 'string'
    );
  };
  const existing = pending.get(key);
  if (matches(existing)) return existing;
  try {
    const saved: unknown = JSON.parse(storage().getItem(key) || 'null');
    if (matches(saved)) {
      pending.set(key, saved);
      return saved;
    }
  } catch {
    /* Storage is optional. */
  }
  const request = { preview_token: token, expected_version: version, request_id: uuid() };
  pending.set(key, request);
  try {
    storage().setItem(key, JSON.stringify(request));
  } catch {
    /* In-memory retry keeps the same key. */
  }
  return request;
}
