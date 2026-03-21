/**
 * SessionStarter — form to create a new investigation session.
 * Accepts a seed address + chain, calls POST /sessions, then shows the graph.
 */

import { useState } from 'react';
import { createSession } from '../api/client';
import { useGraphStore } from '../store/graphStore';

const CHAINS = [
  'ethereum', 'bitcoin', 'solana', 'bsc', 'polygon', 'arbitrum',
  'optimism', 'base', 'avalanche', 'injective', 'starknet',
  'tron', 'xrp', 'cosmos', 'sui',
  'litecoin', 'bitcoin_cash', 'dogecoin',
];

interface Props {
  onSessionCreated: (sessionId: string) => void;
}

export default function SessionStarter({ onSessionCreated }: Props) {
  const [address, setAddress] = useState('');
  const [chain, setChain] = useState('ethereum');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { initSession } = useGraphStore();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!address.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await createSession({ seed_address: address.trim(), seed_chain: chain });
      initSession(resp.session_id, resp.root_node);
      onSessionCreated(resp.session_id);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to create session';
      setError(message === 'Not authenticated' ? 'Redirecting to login…' : message);
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
        <div style={{ color: '#94a3b8', fontSize: 11, lineHeight: 1.5, textAlign: 'center' }}>
          Local standalone graph stacks only expand activity that is already loaded into
          the current event-store dataset.
        </div>
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
