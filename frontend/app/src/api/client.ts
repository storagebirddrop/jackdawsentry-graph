/**
 * Jackdaw Sentry — Investigation Graph API client.
 *
 * Thin wrapper around the v2 session / expansion endpoints.
 * Auth token is read from sessionStorage (set by the graph login page).
 */

import type {
  AssetCatalogResponse,
  SessionCreateRequest,
  SessionCreateResponse,
  InvestigationSessionResponse,
  RecentSessionsResponse,
  WorkspaceSnapshotV1,
  SessionSnapshotResponse,
  ExpandRequest,
  ExpansionResponseV2,
  BridgeHopStatusResponse,
  IngestStatusResponse,
  TxResolveResponse,
} from '../types/graph';

const API_BASE = '/api/v1';

function decodeJwtPayload(token: string): Record<string, unknown> | null {
  const parts = token.split('.');
  if (parts.length < 2) return null;

  try {
    const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = base64 + '='.repeat((4 - (base64.length % 4 || 4)) % 4);
    return JSON.parse(window.atob(padded)) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function isExpiredToken(token: string): boolean {
  const payload = decodeJwtPayload(token);
  const exp = payload?.exp;
  return typeof exp === 'number' && Date.now() >= exp * 1000;
}

function clearAuthToken(): void {
  sessionStorage.removeItem('jds_token');
  sessionStorage.removeItem('jds_user');
}

function getAuthToken(): string | null {
  const token = sessionStorage.getItem('jds_token');
  if (!token) return null;
  if (isExpiredToken(token)) {
    clearAuthToken();
    return null;
  }
  return token;
}

export function isAuthenticated(): boolean {
  return !!getAuthToken();
}

export function redirectToLogin(): void {
  clearAuthToken();
  window.location.replace('/login');
}

function authHeaders(): HeadersInit {
  const token = getAuthToken();
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

/** Restore an existing investigation session from the backend workspace snapshot. */
export async function getSession(
  sessionId: string,
): Promise<InvestigationSessionResponse> {
  const res = await fetch(`${API_BASE}/graph/sessions/${sessionId}`, {
    headers: authHeaders(),
    credentials: 'same-origin',
  });
  return handleResponse<InvestigationSessionResponse>(res);
}

/** Discover recent backend-owned sessions for restore. */
export async function getRecentSessions(
  limit = 5,
): Promise<RecentSessionsResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  const res = await fetch(`${API_BASE}/graph/sessions/recent?${params.toString()}`, {
    headers: authHeaders(),
    credentials: 'same-origin',
  });
  return handleResponse<RecentSessionsResponse>(res);
}

/** Persist the current investigation workspace to the backend session snapshot. */
export async function saveSessionSnapshot(
  sessionId: string,
  snapshot: WorkspaceSnapshotV1,
): Promise<SessionSnapshotResponse> {
  const res = await fetch(`${API_BASE}/graph/sessions/${sessionId}/snapshot`, {
    method: 'POST',
    headers: authHeaders(),
    credentials: 'same-origin',
    body: JSON.stringify(snapshot),
  });
  return handleResponse<SessionSnapshotResponse>(res);
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

/** Load the asset catalog available to the current session. */
export async function getSessionAssets(
  sessionId: string,
  chains: string[] = [],
): Promise<AssetCatalogResponse> {
  const params = new URLSearchParams();
  for (const chain of chains) {
    const normalized = chain.trim().toLowerCase();
    if (normalized) params.append('chains', normalized);
  }
  const suffix = params.toString() ? `?${params.toString()}` : '';
  const res = await fetch(
    `${API_BASE}/graph/sessions/${sessionId}/assets${suffix}`,
    { headers: authHeaders(), credentials: 'same-origin' },
  );
  return handleResponse<AssetCatalogResponse>(res);
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

/** Poll background address ingest job status.
 *
 * Called every 5 s when expansion returns ingest_pending=true.
 * When status becomes 'completed', the caller should retry the expansion.
 */
export async function getIngestStatus(
  sessionId: string,
  address: string,
  chain: string,
): Promise<IngestStatusResponse> {
  const params = new URLSearchParams({ address, chain });
  const res = await fetch(
    `${API_BASE}/graph/sessions/${sessionId}/ingest/status?${params.toString()}`,
    { headers: authHeaders(), credentials: 'same-origin' },
  );
  return handleResponse<IngestStatusResponse>(res);
}

/** Resolve a transaction hash to its sender and receiver addresses.
 *
 * Returns found=false when neither the event store nor the live RPC
 * can locate the transaction.
 */
export async function resolveTx(
  chain: string,
  txHash: string,
): Promise<TxResolveResponse> {
  const params = new URLSearchParams({ chain, tx: txHash });
  const res = await fetch(
    `${API_BASE}/graph/resolve-tx?${params.toString()}`,
    { headers: authHeaders(), credentials: 'same-origin' },
  );
  return handleResponse<TxResolveResponse>(res);
}
