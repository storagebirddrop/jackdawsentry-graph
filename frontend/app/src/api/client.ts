/**
 * Jackdaw Sentry — Investigation Graph API client.
 *
 * Thin wrapper around the v2 session / expansion endpoints.
 * Auth token is read from sessionStorage (set by the graph login page).
 */

import type {
  SessionCreateRequest,
  SessionCreateResponse,
  ExpandRequest,
  ExpansionResponseV2,
  BridgeHopStatusResponse,
} from '../types/graph';

const API_BASE = '/api/v1';

function getAuthToken(): string | null {
  return sessionStorage.getItem('jds_token');
}

export function isAuthenticated(): boolean {
  return !!getAuthToken();
}

export function redirectToLogin(): void {
  window.location.href = '/login';
}

function authHeaders(): HeadersInit {
  const token = getAuthToken();
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    redirectToLogin();
    throw new Error('Not authenticated');
  }
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

function normalizeExpansionResponse(
  payload: ExpansionResponseV2,
): ExpansionResponseV2 {
  return {
    ...payload,
    nodes: payload.nodes ?? payload.added_nodes ?? [],
    edges: payload.edges ?? payload.added_edges ?? [],
  };
}

/** Create a new investigation session seeded from a single address. */
export async function createSession(
  req: SessionCreateRequest,
): Promise<SessionCreateResponse> {
  const res = await fetch(`${API_BASE}/graph/sessions`, {
    method: 'POST',
    headers: authHeaders(),
    credentials: 'same-origin',
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
    credentials: 'same-origin',
    body: JSON.stringify(req),
  });
  return normalizeExpansionResponse(await handleResponse<ExpansionResponseV2>(res));
}

/** Poll bridge hop resolution status. */
export async function getBridgeHopStatus(
  sessionId: string,
  hopId: string,
): Promise<BridgeHopStatusResponse> {
  const res = await fetch(
    `${API_BASE}/graph/sessions/${sessionId}/hops/${hopId}/status`,
    { headers: authHeaders(), credentials: 'same-origin' },
  );
  return handleResponse<BridgeHopStatusResponse>(res);
}
