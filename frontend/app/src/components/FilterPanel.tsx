/**
 * FilterPanel — investigation graph filter controls.
 *
 * Filters are applied client-side against the current graph state.
 * They do NOT re-issue API calls; they hide nodes/edges that don't match.
 *
 * Filter dimensions:
 * - Minimum fiat value (USD) on edges
 * - Asset symbol (free-text, substring match)
 * - Chain (multi-select)
 * - Maximum depth
 */

import { useState } from 'react';

export interface FilterState {
  minFiatValue: number;
  assetFilter: string;
  chainFilter: string[];
  maxDepth: number;
}

export const DEFAULT_FILTERS: FilterState = {
  minFiatValue: 0,
  assetFilter: '',
  chainFilter: [],
  maxDepth: 20,
};

const KNOWN_CHAINS = [
  'ethereum', 'bitcoin', 'solana', 'bsc', 'polygon', 'arbitrum',
  'optimism', 'base', 'avalanche', 'tron', 'xrp',
];

interface Props {
  filters: FilterState;
  onChange: (f: FilterState) => void;
  visible: boolean;
  onClose: () => void;
}

export default function FilterPanel({ filters, onChange, visible, onClose }: Props) {
  const [local, setLocal] = useState<FilterState>(filters);

  if (!visible) return null;

  function apply() {
    onChange(local);
    onClose();
  }

  function reset() {
    setLocal(DEFAULT_FILTERS);
    onChange(DEFAULT_FILTERS);
  }

  function toggleChain(chain: string) {
    setLocal((f) => ({
      ...f,
      chainFilter: f.chainFilter.includes(chain)
        ? f.chainFilter.filter((c) => c !== chain)
        : [...f.chainFilter, chain],
    }));
  }

  return (
    <div
      style={{
        position: 'absolute',
        top: 60,
        left: 16,
        zIndex: 100,
        background: '#1e293b',
        border: '1px solid #334155',
        borderRadius: 8,
        padding: 16,
        minWidth: 240,
        color: '#f1f5f9',
        fontFamily: 'sans-serif',
        fontSize: 12,
        boxShadow: '0 4px 24px rgba(0,0,0,0.5)',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
        <span style={{ fontWeight: 700, fontSize: 13 }}>Filters</span>
        <button onClick={onClose} style={closeBtnStyle}>✕</button>
      </div>

      {/* Fiat threshold */}
      <label style={labelStyle}>
        Min fiat value (USD)
        <input
          type="number"
          min={0}
          value={local.minFiatValue}
          onChange={(e) => setLocal({ ...local, minFiatValue: Number(e.target.value) })}
          style={inputStyle}
        />
      </label>

      {/* Asset filter */}
      <label style={labelStyle}>
        Asset (symbol, substring)
        <input
          type="text"
          placeholder="e.g. USDC"
          value={local.assetFilter}
          onChange={(e) => setLocal({ ...local, assetFilter: e.target.value })}
          style={inputStyle}
        />
      </label>

      {/* Max depth */}
      <label style={labelStyle}>
        Max depth: {local.maxDepth}
        <input
          type="range"
          min={1}
          max={20}
          value={local.maxDepth}
          onChange={(e) => setLocal({ ...local, maxDepth: Number(e.target.value) })}
          style={{ width: '100%', marginTop: 4 }}
        />
      </label>

      {/* Chain filter */}
      <div style={{ marginTop: 8 }}>
        <div style={{ color: '#94a3b8', marginBottom: 4 }}>Chains</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {KNOWN_CHAINS.map((c) => (
            <button
              key={c}
              onClick={() => toggleChain(c)}
              style={{
                padding: '2px 8px',
                borderRadius: 4,
                fontSize: 10,
                border: '1px solid #475569',
                background: local.chainFilter.includes(c) ? '#2563eb' : '#0f172a',
                color: local.chainFilter.includes(c) ? '#fff' : '#94a3b8',
                cursor: 'pointer',
              }}
            >
              {c}
            </button>
          ))}
        </div>
      </div>

      {/* Actions */}
      <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
        <button onClick={apply} style={applyBtnStyle}>Apply</button>
        <button onClick={reset} style={resetBtnStyle}>Reset</button>
      </div>
    </div>
  );
}

const labelStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 3,
  marginBottom: 10,
  color: '#94a3b8',
};

const inputStyle: React.CSSProperties = {
  padding: '5px 8px',
  background: '#0f172a',
  border: '1px solid #334155',
  borderRadius: 4,
  color: '#f1f5f9',
  fontSize: 12,
  outline: 'none',
};

const closeBtnStyle: React.CSSProperties = {
  background: 'none',
  border: 'none',
  color: '#64748b',
  cursor: 'pointer',
  fontSize: 14,
  padding: 0,
};

const applyBtnStyle: React.CSSProperties = {
  flex: 1,
  padding: '6px 0',
  background: '#2563eb',
  border: 'none',
  borderRadius: 5,
  color: '#fff',
  fontSize: 12,
  cursor: 'pointer',
};

const resetBtnStyle: React.CSSProperties = {
  flex: 1,
  padding: '6px 0',
  background: '#334155',
  border: 'none',
  borderRadius: 5,
  color: '#94a3b8',
  fontSize: 12,
  cursor: 'pointer',
};
