/**
 * SessionStarter — form to create a new investigation session.
 * Accepts a seed address + chain, calls POST /sessions, then shows the graph.
 */

import { useEffect, useState } from 'react';
import { createSession } from '../api/client';
import { useGraphStore } from '../store/graphStore';
import type { RecentSessionSummary } from '../types/graph';

const CHAINS = [
  'ethereum', 'bitcoin', 'solana', 'bsc', 'polygon', 'arbitrum',
  'optimism', 'base', 'avalanche', 'tron', 'xrp', 'litecoin',
];

interface Props {
  onSessionCreated: (session: {
    sessionId: string;
    initialWorkspaceRevision: number;
    initialSavedAt: string | null;
    initialRestoreNotice: { tone: 'info' | 'error'; message: string } | null;
  }) => void;
  onRestoreWorkspace?: () => void;
  restoreCandidate?: RecentSessionSummary | null;
}

function detectPreferredChain(address: string): string | null {
  const value = address.trim();
  if (!value) return null;

  if (/^T[1-9A-HJ-NP-Za-km-z]{25,35}$/.test(value)) {
    return 'tron';
  }

  if (/^(bc1[ac-hj-np-z02-9]{11,71}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})$/.test(value)) {
    return 'bitcoin';
  }

  return null;
}

export default function SessionStarter({
  onSessionCreated,
  onRestoreWorkspace,
  restoreCandidate,
}: Props) {
  const [address, setAddress] = useState('');
  const [chain, setChain] = useState('ethereum');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chainHint, setChainHint] = useState<string | null>(null);
  const { initSession } = useGraphStore();

  useEffect(() => {
    const detected = detectPreferredChain(address);
    if (!detected) {
      setChainHint(null);
      return;
    }

    if (chain !== detected) {
      setChain(detected);
      setChainHint(`Detected a ${detected.toUpperCase()} address and switched the investigation chain automatically.`);
      return;
    }

    setChainHint(`Address format matches ${detected.toUpperCase()}.`);
  }, [address, chain]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!address.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await createSession({ seed_address: address.trim(), seed_chain: chain });
      initSession(resp.session_id, resp.root_node);
      onSessionCreated({
        sessionId: resp.session_id,
        initialWorkspaceRevision: 0,
        initialSavedAt: resp.created_at ?? null,
        initialRestoreNotice: null,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to create session';
      setError(message);
    } finally {
      setLoading(false);
    }
  }

  const isDisabled = loading || !address.trim();

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100vh',
        background: '#0f172a',
        color: '#f1f5f9',
        fontFamily: 'sans-serif',
        gap: 16,
      }}
    >
      <h1 style={{ fontSize: 24, margin: 0, color: '#60a5fa' }}>Jackdaw Sentry</h1>
      <p style={{ color: '#94a3b8', margin: 0 }}>Investigation Graph v2</p>

      {restoreCandidate && onRestoreWorkspace && (
        <div
          style={{
            width: 360,
            marginTop: 8,
            padding: '12px 14px',
            borderRadius: 12,
            border: '1px solid rgba(96, 165, 250, 0.28)',
            background: 'rgba(15, 23, 42, 0.72)',
            display: 'grid',
            gap: 8,
          }}
        >
          <div style={{ color: '#bfdbfe', fontWeight: 700, fontSize: 13 }}>
            Restore last workspace
          </div>
          <div style={{ color: '#94a3b8', fontSize: 12, lineHeight: 1.5 }}>
            {restoreCandidate.seed_chain && restoreCandidate.seed_address
              ? `Resume ${restoreCandidate.seed_chain} session ${restoreCandidate.seed_address}.`
              : 'Resume the most recent server-backed investigation workspace.'}
          </div>
          <button
            type="button"
            onClick={onRestoreWorkspace}
            style={{
              ...buttonStyle,
              background: '#1d4ed8',
            }}
          >
            Restore Saved Workspace
          </button>
        </div>
      )}

      <form
        onSubmit={handleSubmit}
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
          width: 360,
          marginTop: 16,
        }}
      >
        <input
          type="text"
          aria-label="Seed address"
          placeholder="Seed address (0x... or bitcoin...)"
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          style={inputStyle}
          onFocus={(e) => {
            if (e.target instanceof HTMLElement) {
              e.target.style.boxShadow = '0 0 0 2px #60a5fa';
              e.target.style.borderColor = '#60a5fa';
            }
          }}
          onBlur={(e) => {
            if (e.target instanceof HTMLElement) {
              e.target.style.boxShadow = 'none';
              e.target.style.borderColor = '#334155';
            }
          }}
        />

        <select
          value={chain}
          onChange={(e) => setChain(e.target.value)}
          style={inputStyle}
          onFocus={(e) => {
            if (e.target instanceof HTMLElement) {
              e.target.style.boxShadow = '0 0 0 2px #60a5fa';
              e.target.style.borderColor = '#60a5fa';
            }
          }}
          onBlur={(e) => {
            if (e.target instanceof HTMLElement) {
              e.target.style.boxShadow = 'none';
              e.target.style.borderColor = '#334155';
            }
          }}
        >
          {CHAINS.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>

        <button
          type="submit"
          disabled={isDisabled}
          style={{
            ...buttonStyle,
            background: isDisabled ? '#1e40af' : '#2563eb',
            cursor: isDisabled ? 'not-allowed' : 'pointer',
          }}
          onFocus={(e) => {
            if (e.target instanceof HTMLElement) {
              e.target.style.boxShadow = '0 0 0 2px #60a5fa';
            }
          }}
          onBlur={(e) => {
            if (e.target instanceof HTMLElement) {
              e.target.style.boxShadow = 'none';
            }
          }}
        >
          {loading ? 'Starting…' : 'Start Investigation'}
        </button>

        {error && (
          <div style={{ color: '#f87171', fontSize: 12, textAlign: 'center' }}>
            {error}
          </div>
        )}
        {chainHint && !error && (
          <div style={{ color: '#93c5fd', fontSize: 12, textAlign: 'center' }}>
            {chainHint}
          </div>
        )}
      </form>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  padding: '8px 12px',
  background: '#1e293b',
  border: '1px solid #334155',
  borderRadius: 6,
  color: '#f1f5f9',
  fontSize: 13,
  outline: 'none',
};

// Add focus-visible styles via style override
const buttonStyle: React.CSSProperties = {
  padding: '10px 0',
  background: '#2563eb',
  border: 'none',
  borderRadius: 6,
  color: '#fff',
  fontSize: 14,
  cursor: 'pointer',
  outline: 'none',
};
