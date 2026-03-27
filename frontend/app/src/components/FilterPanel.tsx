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

import { useEffect, useState } from 'react';
import { bridgeProtocolLabel } from './graphVisuals';

export interface FilterState {
  minFiatValue: number | null;
  assetFilter: string;
  selectedAssets: string[];
  chainFilter: string[];
  maxDepth: number;
  bridgeProtocols: string[];
  bridgeStatuses: Array<'pending' | 'completed' | 'failed'>;
  bridgeRoute: string | null;
}

export const DEFAULT_FILTERS: FilterState = {
  minFiatValue: 0,
  assetFilter: '',
  selectedAssets: [],
  chainFilter: [],
  maxDepth: 20,
  bridgeProtocols: [],
  bridgeStatuses: [],
  bridgeRoute: null,
};

const KNOWN_CHAINS = [
  'ethereum', 'bitcoin', 'solana', 'bsc', 'polygon', 'arbitrum',
  'optimism', 'base', 'avalanche', 'tron', 'xrp',
];

interface Props {
  filters: FilterState;
  onChange: (f: FilterState) => void;
  onClose: () => void;
  availableAssets: string[];
  availableBridgeProtocols: string[];
  availableBridgeRoutes: string[];
}

const BRIDGE_STATUSES: Array<'pending' | 'completed' | 'failed'> = [
  'pending',
  'completed',
  'failed',
];

export default function FilterPanel({
  filters,
  onChange,
  onClose,
  availableAssets,
  availableBridgeProtocols,
  availableBridgeRoutes,
}: Props) {
  const [local, setLocal] = useState<FilterState>(filters);

  useEffect(() => {
    setLocal(filters);
  }, [filters]);

  function apply() {
    onChange(local);
    onClose();
  }

  function reset() {
    setLocal(DEFAULT_FILTERS);
    onChange(DEFAULT_FILTERS);
    onClose();
  }

  function toggleChain(chain: string) {
    setLocal((f) => ({
      ...f,
      chainFilter: f.chainFilter.includes(chain)
        ? f.chainFilter.filter((c) => c !== chain)
        : [...f.chainFilter, chain],
    }));
  }

  function toggleBridgeProtocol(protocol: string) {
    setLocal((f) => ({
      ...f,
      bridgeProtocols: f.bridgeProtocols.includes(protocol)
        ? f.bridgeProtocols.filter((value) => value !== protocol)
        : [...f.bridgeProtocols, protocol],
    }));
  }

  function toggleBridgeStatus(status: 'pending' | 'completed' | 'failed') {
    setLocal((f) => ({
      ...f,
      bridgeStatuses: f.bridgeStatuses.includes(status)
        ? f.bridgeStatuses.filter((value) => value !== status)
        : [...f.bridgeStatuses, status],
    }));
  }

  function toggleBridgeRoute(route: string) {
    setLocal((f) => ({
      ...f,
      bridgeRoute: f.bridgeRoute === route ? null : route,
    }));
  }

  function toggleAsset(asset: string) {
    setLocal((f) => ({
      ...f,
      selectedAssets: f.selectedAssets.includes(asset)
        ? f.selectedAssets.filter((value) => value !== asset)
        : [...f.selectedAssets, asset],
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
          value={local.minFiatValue ?? ''}
          onChange={(e) => {
            const raw = e.target.value;
            const parsed = raw === '' ? null : Number(raw);
            // Prevent NaN from being set (e.g., for "-" or "1." inputs)
            // Clamp negative values to 0
            const clampedValue = Number.isNaN(parsed) ? null : (parsed !== null && parsed < 0 ? 0 : parsed);
            setLocal({ ...local, minFiatValue: clampedValue });
          }}
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

      <div style={{ marginTop: 8 }}>
        <div style={{ color: '#94a3b8', marginBottom: 4 }}>Selected assets</div>
        {availableAssets.length === 0 ? (
          <div style={{ color: '#64748b', fontSize: 11 }}>
            Asset chips appear here once token or native transfer edges are on the canvas.
          </div>
        ) : (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {availableAssets.map((asset) => (
              <button
                key={asset}
                onClick={() => toggleAsset(asset)}
                aria-pressed={local.selectedAssets.includes(asset)}
                style={{
                  ...tokenStyle,
                  background: local.selectedAssets.includes(asset) ? '#0f766e' : '#0f172a',
                  color: local.selectedAssets.includes(asset) ? '#fff' : '#99f6e4',
                  borderColor: local.selectedAssets.includes(asset) ? '#14b8a6' : '#115e59',
                }}
              >
                {asset}
              </button>
            ))}
          </div>
        )}
      </div>

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
              aria-pressed={local.chainFilter.includes(c)}
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

      {(availableBridgeProtocols.length > 0 || availableBridgeRoutes.length > 0) && (
        <>
          <div style={{ marginTop: 12 }}>
            <div style={{ color: '#94a3b8', marginBottom: 4 }}>Bridge protocols</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              {availableBridgeProtocols.map((protocol) => (
                <button
                  key={protocol}
                  onClick={() => toggleBridgeProtocol(protocol)}
                  aria-pressed={local.bridgeProtocols.includes(protocol)}
                  style={{
                    ...tokenStyle,
                    background: local.bridgeProtocols.includes(protocol) ? '#7c3aed' : '#0f172a',
                    color: local.bridgeProtocols.includes(protocol) ? '#fff' : '#c4b5fd',
                    borderColor: local.bridgeProtocols.includes(protocol) ? '#8b5cf6' : '#4c1d95',
                  }}
                >
                  {bridgeProtocolLabel(protocol)}
                </button>
              ))}
            </div>
          </div>

          <div style={{ marginTop: 12 }}>
            <div style={{ color: '#94a3b8', marginBottom: 4 }}>Bridge status</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              {BRIDGE_STATUSES.map((status) => (
                <button
                  key={status}
                  onClick={() => toggleBridgeStatus(status)}
                  aria-pressed={local.bridgeStatuses.includes(status)}
                  style={{
                    ...tokenStyle,
                    background: local.bridgeStatuses.includes(status) ? '#2563eb' : '#0f172a',
                    color: local.bridgeStatuses.includes(status) ? '#fff' : '#bfdbfe',
                    borderColor: local.bridgeStatuses.includes(status) ? '#60a5fa' : '#1d4ed8',
                    textTransform: 'capitalize',
                  }}
                >
                  {status}
                </button>
              ))}
            </div>
          </div>

          <div style={{ marginTop: 12 }}>
            <div style={{ color: '#94a3b8', marginBottom: 4 }}>Bridge route focus</div>
            {availableBridgeRoutes.length === 0 ? (
              <div style={{ color: '#64748b', fontSize: 11 }}>No bridge routes in the current graph.</div>
            ) : (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {availableBridgeRoutes.map((route) => (
                  <button
                    key={route}
                    onClick={() => toggleBridgeRoute(route)}
                    aria-pressed={local.bridgeRoute === route}
                    style={{
                      ...tokenStyle,
                      background: local.bridgeRoute === route ? '#0f766e' : '#0f172a',
                      color: local.bridgeRoute === route ? '#fff' : '#99f6e4',
                      borderColor: local.bridgeRoute === route ? '#14b8a6' : '#115e59',
                    }}
                  >
                    {route}
                  </button>
                ))}
              </div>
            )}
          </div>
        </>
      )}

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

const tokenStyle: React.CSSProperties = {
  padding: '3px 8px',
  borderRadius: 999,
  fontSize: 10,
  border: '1px solid',
  cursor: 'pointer',
};
