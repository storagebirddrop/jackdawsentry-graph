/**
 * SessionStarter — form to create a new investigation session.
 *
 * Accepts either a seed address OR a transaction hash + chain.
 * When a tx hash is entered, the form resolves it to sender/receiver
 * and lets the investigator choose which address to seed from.
 */

import { useState, useEffect } from 'react';
import { createSession, resolveTx } from '../api/client';
import { useGraphStore } from '../store/graphStore';
import type { TxResolveResponse } from '../types/graph';

const CHAINS = [
  'ethereum', 'bitcoin', 'solana', 'bsc', 'polygon', 'arbitrum',
  'optimism', 'base', 'avalanche', 'injective', 'starknet',
  'tron', 'xrp', 'cosmos', 'sui',
  'litecoin', 'bitcoin_cash', 'dogecoin',
];

interface Props {
  onSessionCreated: (sessionId: string) => void;
}

/** Return 'address', 'tx_hash', or null based on format heuristics. */
function detectInputType(value: string): 'address' | 'tx_hash' | null {
  const v = value.trim();
  if (!v) return null;
  // EVM tx hash: 0x + 64 hex
  if (/^0x[0-9a-fA-F]{64}$/.test(v)) return 'tx_hash';
  // EVM address: 0x + 40 hex
  if (/^0x[0-9a-fA-F]{40}$/.test(v)) return 'address';
  // Bare 64 hex (Bitcoin/Tron tx hash without 0x)
  if (/^[0-9a-fA-F]{64}$/.test(v)) return 'tx_hash';
  // Solana tx signature: base58, ~87-88 chars
  if (/^[1-9A-HJ-NP-Za-km-z]{80,}$/.test(v)) return 'tx_hash';
  // Solana / Bitcoin / Tron address: base58, shorter
  if (/^[1-9A-HJ-NP-Za-km-z]{25,60}$/.test(v)) return 'address';
  return null;
}

function shortHash(h: string, chars = 10): string {
  if (h.length <= chars * 2 + 3) return h;
  return `${h.slice(0, chars)}...${h.slice(-chars)}`;
}

function formatValue(value: number | undefined, symbol: string | undefined): string {
  if (value == null || value === 0) return '';
  const formatted = value < 0.001 ? value.toExponential(3) : value.toFixed(6).replace(/\.?0+$/, '');
  return `${formatted} ${symbol ?? ''}`.trim();
}

export default function SessionStarter({ onSessionCreated }: Props) {
  const [input, setInput] = useState('');
  const [chain, setChain] = useState('ethereum');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resolved, setResolved] = useState<TxResolveResponse | null>(null);
  const { initSession } = useGraphStore();

  const inputType = detectInputType(input);

  // Clear resolved tx whenever input or chain changes.
  useEffect(() => {
    setResolved(null);
    setError(null);
  }, [input, chain]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed) return;

    if (inputType === null) {
      setError('Unrecognized format — enter a valid address or transaction hash.');
      return;
    }

    setError(null);

    if (inputType === 'tx_hash') {
      // Resolve the tx hash first to let the user pick sender/receiver.
      setLoading(true);
      try {
        const result = await resolveTx(chain, trimmed);
        if (!result.found) {
          setError(
            'Transaction not found on this chain. Check the hash and chain selection, or try again once the transaction is indexed.',
          );
        } else {
          setResolved(result);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to resolve transaction');
      } finally {
        setLoading(false);
      }
      return;
    }

    // Address path — create session directly.
    await startSession(trimmed);
  }

  async function startSession(address: string) {
    setLoading(true);
    setError(null);
    try {
      const resp = await createSession({ seed_address: address, seed_chain: chain });
      initSession(resp.session_id, resp.root_node);
      onSessionCreated(resp.session_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create session');
    } finally {
      setLoading(false);
    }
  }

  const isDisabled = loading || !input.trim() || inputType === null;

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
      <img
        src="/favicon.svg"
        alt="Jackdaw Sentry"
        style={{ width: 56, height: 56, marginBottom: 4 }}
      />
      <h1 style={{ fontSize: 24, margin: 0, color: '#60a5fa' }}>Jackdaw Sentry</h1>
      <p style={{ color: '#94a3b8', margin: 0 }}>Investigation Graph — open source</p>

      <form
        onSubmit={handleSubmit}
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
          width: 420,
          marginTop: 16,
        }}
      >
        {/* Input type badge */}
        <div style={{ position: 'relative' }}>
          <input
            type="text"
            aria-label="Address or transaction hash"
            placeholder="Address or transaction hash"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            style={{ ...inputStyle, width: '100%', boxSizing: 'border-box', paddingRight: inputType ? 110 : 12 }}
            onFocus={(e) => {
              e.target.style.boxShadow = '0 0 0 2px #60a5fa';
              e.target.style.borderColor = '#60a5fa';
            }}
            onBlur={(e) => {
              e.target.style.boxShadow = 'none';
              e.target.style.borderColor = '#334155';
            }}
          />
          {inputType && (
            <span
              style={{
                position: 'absolute',
                right: 10,
                top: '50%',
                transform: 'translateY(-50%)',
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: '0.06em',
                textTransform: 'uppercase',
                padding: '2px 7px',
                borderRadius: 999,
                background: inputType === 'tx_hash' ? '#7c3aed22' : '#16533222',
                color: inputType === 'tx_hash' ? '#a78bfa' : '#34d399',
                border: `1px solid ${inputType === 'tx_hash' ? '#7c3aed55' : '#34d39955'}`,
                pointerEvents: 'none',
              }}
            >
              {inputType === 'tx_hash' ? 'TX hash' : 'Address'}
            </span>
          )}
        </div>

        {input.trim() && inputType === null && (
          <div style={{ color: '#fbbf24', fontSize: 11, marginTop: -4 }}>
            Unrecognized format — enter a valid address or transaction hash.
          </div>
        )}

        <select
          value={chain}
          onChange={(e) => setChain(e.target.value)}
          style={inputStyle}
          onFocus={(e) => {
            e.target.style.boxShadow = '0 0 0 2px #60a5fa';
            e.target.style.borderColor = '#60a5fa';
          }}
          onBlur={(e) => {
            e.target.style.boxShadow = 'none';
            e.target.style.borderColor = '#334155';
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
          onFocus={(e) => { e.target.style.boxShadow = '0 0 0 2px #60a5fa'; }}
          onBlur={(e) => { e.target.style.boxShadow = 'none'; }}
        >
          {loading
            ? (inputType === 'tx_hash' ? 'Resolving…' : 'Starting…')
            : (inputType === 'tx_hash' ? 'Resolve Transaction' : 'Start Investigation')}
        </button>

        {error && (
          <div style={{ color: '#f87171', fontSize: 12, textAlign: 'center' }}>{error}</div>
        )}

        {/* Transaction resolution panel */}
        {resolved && (
          <div style={resolvedPanelStyle}>
            <div style={{ fontSize: 11, fontWeight: 700, color: '#94a3b8', letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 10 }}>
              Transaction resolved
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 12 }}>
              <div style={txRowStyle}>
                <span style={txLabelStyle}>Hash</span>
                <span style={{ ...txValueStyle, fontFamily: 'monospace', fontSize: 11 }}>{shortHash(resolved.tx_hash)}</span>
              </div>
              {resolved.value_native != null && resolved.value_native > 0 && (
                <div style={txRowStyle}>
                  <span style={txLabelStyle}>Value</span>
                  <span style={txValueStyle}>{formatValue(resolved.value_native, resolved.asset_symbol)}</span>
                </div>
              )}
              {resolved.timestamp && (
                <div style={txRowStyle}>
                  <span style={txLabelStyle}>Time</span>
                  <span style={txValueStyle}>{new Date(resolved.timestamp).toLocaleString()}</span>
                </div>
              )}
              {resolved.status && (
                <div style={txRowStyle}>
                  <span style={txLabelStyle}>Status</span>
                  <span style={{ ...txValueStyle, color: resolved.status === 'success' ? '#34d399' : resolved.status === 'failed' ? '#f87171' : '#94a3b8' }}>
                    {resolved.status}
                  </span>
                </div>
              )}
            </div>

            <div style={{ fontSize: 11, color: '#64748b', marginBottom: 8 }}>
              Choose which participant to investigate:
            </div>

            <div style={{ display: 'flex', gap: 8, flexDirection: 'column' }}>
              {resolved.from_address && (
                <button
                  type="button"
                  onClick={() => void startSession(resolved.from_address!)}
                  disabled={loading}
                  style={participantBtnStyle}
                >
                  <span style={{ fontSize: 10, color: '#94a3b8', display: 'block', marginBottom: 2 }}>SENDER</span>
                  <span style={{ fontFamily: 'monospace', fontSize: 11, color: '#e2e8f0', wordBreak: 'break-all' }}>
                    {resolved.from_address}
                  </span>
                </button>
              )}
              {resolved.to_address && (
                <button
                  type="button"
                  onClick={() => void startSession(resolved.to_address!)}
                  disabled={loading}
                  style={participantBtnStyle}
                >
                  <span style={{ fontSize: 10, color: '#94a3b8', display: 'block', marginBottom: 2 }}>RECEIVER</span>
                  <span style={{ fontFamily: 'monospace', fontSize: 11, color: '#e2e8f0', wordBreak: 'break-all' }}>
                    {resolved.to_address}
                  </span>
                </button>
              )}
              {!resolved.from_address && !resolved.to_address && (
                <div style={{ color: '#94a3b8', fontSize: 12, textAlign: 'center' }}>
                  Participant addresses not available for this transaction.
                </div>
              )}
            </div>
          </div>
        )}

        <div style={{ color: '#94a3b8', fontSize: 11, lineHeight: 1.5, textAlign: 'center' }}>
          Local standalone graph stacks only expand activity that is already loaded into
          the current event-store dataset.
        </div>
      </form>

      <div style={{ marginTop: 24, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
        <a
          href="https://github.com/storagebirddrop"
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: '#60a5fa', fontSize: 12, textDecoration: 'none' }}
        >
          github.com/storagebirddrop
        </a>
        <div style={{ color: '#64748b', fontSize: 11, textAlign: 'center' }}>
          Support via Lightning / Nostr:{' '}
          <span style={{ color: '#94a3b8', whiteSpace: 'nowrap' }}>
            npub1p0jkd532p3c0za2s7fugq0tx30xm2e4v03n6udkqze6ercyf5fesgsy9fv@npub.cash
          </span>
        </div>
      </div>
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

const resolvedPanelStyle: React.CSSProperties = {
  background: '#1e293b',
  border: '1px solid #334155',
  borderRadius: 8,
  padding: 14,
  marginTop: 4,
};

const txRowStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  gap: 12,
};

const txLabelStyle: React.CSSProperties = {
  fontSize: 11,
  color: '#64748b',
  flexShrink: 0,
  width: 44,
};

const txValueStyle: React.CSSProperties = {
  fontSize: 12,
  color: '#cbd5e1',
  textAlign: 'right',
};

const participantBtnStyle: React.CSSProperties = {
  background: '#0f172a',
  border: '1px solid #334155',
  borderRadius: 6,
  padding: '8px 12px',
  textAlign: 'left',
  cursor: 'pointer',
  transition: 'border-color 0.12s',
  outline: 'none',
  width: '100%',
};
