/**
 * Jackdaw Sentry — Investigation Graph API client.
 *
 * Thin wrapper around the v2 session / expansion endpoints.
 * Auth token is read from localStorage (set by the existing login page).
 */

import type {
  SessionCreateRequest,
  SessionCreateResponse,
  ExpandRequest,
  ExpansionResponseV2,
  BridgeHopStatusResponse,
} from '../types/graph';

const API_BASE = '/api/v1';

function authHeaders(): HeadersInit {
  const token = localStorage.getItem('access_token');
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

/** Create a new investigation session seeded from a single address. */
export async function createSession(
  req: SessionCreateRequest,
): Promise<SessionCreateResponse> {
  const res = await fetch(`${API_BASE}/graph/sessions`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(req),
  });
  return handleResponse<SessionCreateResponse>(res);
}

/** Expand a node in an existing session. */
export async function expandNode(
  sessionId: string,
  req: ExpandRequest,
): Promise<ExpansionResponseV2> {
  const res = await fetch(`${API_BASE}/graph/sessions/${sessionId}/expand`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(req),
  });
  return handleResponse<ExpansionResponseV2>(res);
}

/** Poll bridge hop resolution status. */
export async function getBridgeHopStatus(
  sessionId: string,
  hopId: string,
): Promise<BridgeHopStatusResponse> {
  const res = await fetch(
    `${API_BASE}/graph/sessions/${sessionId}/hops/${hopId}/status`,
    { headers: authHeaders() },
  );
  return handleResponse<BridgeHopStatusResponse>(res);
}
