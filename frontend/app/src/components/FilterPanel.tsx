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
import type { AssetCatalogItem } from '../types/graph';

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

export type AssetCatalogScopeMode = 'session' | 'visible';
export const DEFAULT_ASSET_CATALOG_SCOPE: AssetCatalogScopeMode = 'session';

const KNOWN_CHAINS = [
  'ethereum', 'bitcoin', 'solana', 'bsc', 'polygon', 'arbitrum',
  'optimism', 'base', 'avalanche', 'tron', 'xrp',
];

interface Props {
  filters: FilterState;
  onChange: (f: FilterState) => void;
  onClose: () => void;
  availableAssets: AssetCatalogItem[];
  sessionAssetCount: number;
  visibleAssetCount: number;
  assetCatalogScope: AssetCatalogScopeMode;
  onAssetCatalogScopeChange: (scope: AssetCatalogScopeMode) => void;
  pinnedAssetKeys: string[];
  onPinnedAssetKeysChange: (keys: string[]) => void;
  availableBridgeProtocols: string[];
  availableBridgeRoutes: string[];
}

const BRIDGE_STATUSES: Array<'pending' | 'completed' | 'failed'> = [
  'pending',
  'completed',
  'failed',
];

const ASSET_GROUP_ORDER: Array<AssetCatalogItem['variant_kind']> = [
  'native',
  'canonical',
  'wrapped',
  'bridged',
  'unknown',
];

const ASSET_GROUP_LABELS: Record<AssetCatalogItem['variant_kind'], string> = {
  native: 'Native assets',
  canonical: 'Canonical issuers',
  wrapped: 'Wrapped assets',
  bridged: 'Bridged assets',
  unknown: 'Unverified symbols',
};

const IDENTITY_BADGE: Record<AssetCatalogItem['identity_status'], { label: string; color: string; border: string }> = {
  verified: { label: 'Verified', color: '#99f6e4', border: '#115e59' },
  heuristic: { label: 'Heuristic', color: '#fde68a', border: '#92400e' },
  unknown: { label: 'Unknown', color: '#cbd5e1', border: '#334155' },
};

const VARIANT_SORT_ORDER: Record<AssetCatalogItem['variant_kind'], number> = {
  native: 0,
  canonical: 1,
  wrapped: 2,
  bridged: 3,
  unknown: 4,
};

const IDENTITY_SORT_ORDER: Record<AssetCatalogItem['identity_status'], number> = {
  verified: 0,
  heuristic: 1,
  unknown: 2,
};

const UNKNOWN_LONG_TAIL_PREVIEW = 8;

function isLongTailUnknownAsset(asset: AssetCatalogItem): boolean {
  return asset.identity_status === 'unknown' && asset.observed_transfer_count <= 2;
}

function sortAssetsForDisplay(left: AssetCatalogItem, right: AssetCatalogItem): number {
  const leftLongTail = isLongTailUnknownAsset(left);
  const rightLongTail = isLongTailUnknownAsset(right);
  if (leftLongTail !== rightLongTail) {
    return leftLongTail ? 1 : -1;
  }
  if (VARIANT_SORT_ORDER[left.variant_kind] !== VARIANT_SORT_ORDER[right.variant_kind]) {
    return VARIANT_SORT_ORDER[left.variant_kind] - VARIANT_SORT_ORDER[right.variant_kind];
  }
  if (IDENTITY_SORT_ORDER[left.identity_status] !== IDENTITY_SORT_ORDER[right.identity_status]) {
    return IDENTITY_SORT_ORDER[left.identity_status] - IDENTITY_SORT_ORDER[right.identity_status];
  }
  if (right.blockchains.length !== left.blockchains.length) {
    return right.blockchains.length - left.blockchains.length;
  }
  if (right.observed_transfer_count !== left.observed_transfer_count) {
    return right.observed_transfer_count - left.observed_transfer_count;
  }
  return left.symbol.localeCompare(right.symbol);
}

export default function FilterPanel({
  filters,
  onChange,
  onClose,
  availableAssets,
  sessionAssetCount,
  visibleAssetCount,
  assetCatalogScope,
  onAssetCatalogScopeChange,
  pinnedAssetKeys,
  onPinnedAssetKeysChange,
  availableBridgeProtocols,
  availableBridgeRoutes,
}: Props) {
  const [local, setLocal] = useState<FilterState>(filters);
  const [localAssetCatalogScope, setLocalAssetCatalogScope] = useState<AssetCatalogScopeMode>(assetCatalogScope);
  const [localPinnedAssetKeys, setLocalPinnedAssetKeys] = useState<string[]>(pinnedAssetKeys);
  const [showUnknownLongTail, setShowUnknownLongTail] = useState(false);

  useEffect(() => {
    setLocal(filters);
  }, [filters]);

  useEffect(() => {
    setLocalAssetCatalogScope(assetCatalogScope);
  }, [assetCatalogScope]);

  useEffect(() => {
    setLocalPinnedAssetKeys(pinnedAssetKeys);
  }, [pinnedAssetKeys]);

  function apply() {
    onChange(local);
    onAssetCatalogScopeChange(localAssetCatalogScope);
    onPinnedAssetKeysChange(localPinnedAssetKeys);
    onClose();
  }

  function reset() {
    setLocal(DEFAULT_FILTERS);
    setLocalAssetCatalogScope(DEFAULT_ASSET_CATALOG_SCOPE);
    setLocalPinnedAssetKeys([]);
    onChange(DEFAULT_FILTERS);
    onAssetCatalogScopeChange(DEFAULT_ASSET_CATALOG_SCOPE);
    onPinnedAssetKeysChange([]);
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

  function togglePinnedAsset(assetKey: string) {
    setLocalPinnedAssetKeys((current) => (
      current.includes(assetKey)
        ? current.filter((value) => value !== assetKey)
        : [assetKey, ...current.filter((value) => value !== assetKey)].slice(0, 12)
    ));
  }

  const assetSearchQuery = local.assetFilter.trim().toLowerCase();
  const isAssetSearchActive = assetSearchQuery.length > 0;
  const revealUnknownLongTail = showUnknownLongTail || isAssetSearchActive;

  const visibleAssets = availableAssets.filter((asset) => {
    if (!assetSearchQuery) return true;
    const haystack = [
      asset.symbol,
      asset.display_name,
      asset.canonical_asset_id,
      asset.canonical_symbol,
      asset.identity_status,
      asset.variant_kind,
      asset.asset_key,
      asset.sample_asset_address,
      asset.blockchains.join(' '),
      asset.token_standards.join(' '),
    ]
      .filter((value): value is string => Boolean(value))
      .join(' ')
      .toLowerCase();
    return haystack.includes(assetSearchQuery);
  });

  const pinnedAssets = visibleAssets
    .filter((asset) => localPinnedAssetKeys.includes(asset.asset_key))
    .sort(sortAssetsForDisplay);

  const unpinnedVisibleAssets = visibleAssets.filter(
    (asset) => !localPinnedAssetKeys.includes(asset.asset_key),
  );

  const assetGroups = unpinnedVisibleAssets.reduce<Map<string, AssetCatalogItem[]>>((groups, asset) => {
    const groupLabel = ASSET_GROUP_LABELS[asset.variant_kind];
    const existing = groups.get(groupLabel) ?? [];
    existing.push(asset);
    groups.set(groupLabel, existing);
    return groups;
  }, new Map());

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
        Asset search
        <input
          type="text"
          placeholder="e.g. USDC, spl, tron"
          value={local.assetFilter}
          onChange={(e) => setLocal({ ...local, assetFilter: e.target.value })}
          style={inputStyle}
        />
      </label>

      <div style={{ marginTop: 8 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 4 }}>
          <div style={{ color: '#94a3b8' }}>Asset catalog</div>
          <div style={{ color: '#64748b', fontSize: 11 }}>
            {local.selectedAssets.length} selected
          </div>
        </div>
        <div style={{ display: 'flex', gap: 6, marginBottom: 8, flexWrap: 'wrap' }}>
          <button
            type="button"
            onClick={() => setLocalAssetCatalogScope('session')}
            style={{
              ...scopeChipStyle,
              background: localAssetCatalogScope === 'session' ? '#1d4ed8' : '#0f172a',
              color: localAssetCatalogScope === 'session' ? '#eff6ff' : '#93c5fd',
              borderColor: localAssetCatalogScope === 'session' ? '#60a5fa' : '#1e3a8a',
            }}
          >
            Full session · {sessionAssetCount}
          </button>
          <button
            type="button"
            onClick={() => setLocalAssetCatalogScope('visible')}
            style={{
              ...scopeChipStyle,
              background: localAssetCatalogScope === 'visible' ? '#0f766e' : '#0f172a',
              color: localAssetCatalogScope === 'visible' ? '#ecfeff' : '#99f6e4',
              borderColor: localAssetCatalogScope === 'visible' ? '#14b8a6' : '#115e59',
            }}
          >
            Visible lens · {visibleAssetCount}
          </button>
        </div>
        <div style={{ color: '#64748b', fontSize: 11, marginBottom: 6 }}>
          Verified and canonical assets surface first. Search also matches contract and mint addresses.
        </div>
        {availableAssets.length === 0 ? (
          localAssetCatalogScope === 'visible' && sessionAssetCount > 0 ? (
            <div style={{ display: 'grid', gap: 8 }}>
              <div style={{ color: '#64748b', fontSize: 11 }}>
                No assets are visible in the current lens yet. Switch to the full session catalog to browse all indexed assets.
              </div>
              <button
                type="button"
                onClick={() => setLocalAssetCatalogScope('session')}
                style={{
                  ...scopeChipStyle,
                  width: 'fit-content',
                  background: '#1d4ed8',
                  color: '#eff6ff',
                  borderColor: '#60a5fa',
                }}
              >
                Switch to full session
              </button>
            </div>
          ) : (
            <div style={{ color: '#64748b', fontSize: 11 }}>
              The asset catalog will populate from indexed transfers and cached token metadata for this session.
            </div>
          )
        ) : visibleAssets.length === 0 ? (
          <div style={{ color: '#64748b', fontSize: 11 }}>
            No assets match the current search text.
          </div>
        ) : (
          <div style={{ display: 'grid', gap: 10, maxHeight: 240, overflowY: 'auto', paddingRight: 4 }}>
            {pinnedAssets.length > 0 ? (
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center', marginBottom: 6 }}>
                  <div style={{ color: '#64748b', fontSize: 10, fontWeight: 700, textTransform: 'uppercase' }}>
                    Pinned assets
                  </div>
                  <div style={{ color: '#64748b', fontSize: 10 }}>
                    {pinnedAssets.length} pinned
                  </div>
                </div>
                <div style={{ display: 'grid', gap: 6 }}>
                  {pinnedAssets.map((asset) => renderAssetCard({
                    asset,
                    isSelected: local.selectedAssets.includes(asset.asset_key),
                    isPinned: true,
                    onToggleAsset: toggleAsset,
                    onTogglePinnedAsset: togglePinnedAsset,
                  }))}
                </div>
              </div>
            ) : null}
            {Array.from(assetGroups.entries())
              .sort(([left], [right]) => {
                const leftIndex = ASSET_GROUP_ORDER.findIndex((value) => ASSET_GROUP_LABELS[value] === left);
                const rightIndex = ASSET_GROUP_ORDER.findIndex((value) => ASSET_GROUP_LABELS[value] === right);
                return leftIndex - rightIndex;
              })
              .map(([groupLabel, assets]) => {
                const groupVariant = ASSET_GROUP_ORDER.find((value) => ASSET_GROUP_LABELS[value] === groupLabel) ?? 'unknown';
                const sortedAssets = assets.slice().sort(sortAssetsForDisplay);
                const longTailAssets = groupVariant === 'unknown'
                  ? sortedAssets.filter(isLongTailUnknownAsset)
                  : [];
                const featuredAssets = groupVariant === 'unknown'
                  ? sortedAssets.filter((asset) => !isLongTailUnknownAsset(asset))
                  : sortedAssets;
                const hiddenLongTailCount = groupVariant === 'unknown' && !revealUnknownLongTail
                  ? (featuredAssets.length > 0
                    ? longTailAssets.length
                    : Math.max(0, longTailAssets.length - UNKNOWN_LONG_TAIL_PREVIEW))
                  : 0;
                const assetsToRender = groupVariant === 'unknown' && !revealUnknownLongTail
                  ? (featuredAssets.length > 0 ? featuredAssets : longTailAssets.slice(0, UNKNOWN_LONG_TAIL_PREVIEW))
                  : sortedAssets;

                return (
                <div key={groupLabel}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center', marginBottom: 6 }}>
                    <div style={{ color: '#64748b', fontSize: 10, fontWeight: 700, textTransform: 'uppercase' }}>
                      {groupLabel}
                    </div>
                    <div style={{ color: '#64748b', fontSize: 10 }}>
                      {assets.length} assets
                    </div>
                  </div>
                  <div style={{ display: 'grid', gap: 6 }}>
                    {assetsToRender.map((asset) => renderAssetCard({
                      asset,
                      isSelected: local.selectedAssets.includes(asset.asset_key),
                      isPinned: localPinnedAssetKeys.includes(asset.asset_key),
                      onToggleAsset: toggleAsset,
                      onTogglePinnedAsset: togglePinnedAsset,
                    }))}
                    {groupVariant === 'unknown' && hiddenLongTailCount > 0 ? (
                      <button
                        onClick={() => setShowUnknownLongTail(true)}
                        style={{
                          borderRadius: 10,
                          border: '1px dashed #475569',
                          background: '#020617',
                          color: '#94a3b8',
                          cursor: 'pointer',
                          padding: '10px 12px',
                          textAlign: 'left',
                          fontSize: 11,
                        }}
                      >
                        Show {hiddenLongTailCount} low-signal unknown assets
                      </button>
                    ) : null}
                    {groupVariant === 'unknown' && showUnknownLongTail && !isAssetSearchActive && longTailAssets.length > 0 ? (
                      <button
                        onClick={() => setShowUnknownLongTail(false)}
                        style={{
                          borderRadius: 10,
                          border: '1px dashed #475569',
                          background: '#020617',
                          color: '#94a3b8',
                          cursor: 'pointer',
                          padding: '10px 12px',
                          textAlign: 'left',
                          fontSize: 11,
                        }}
                      >
                        Hide low-signal unknown assets
                      </button>
                    ) : null}
                  </div>
                </div>
              );
              })}
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

const scopeChipStyle: React.CSSProperties = {
  padding: '4px 9px',
  borderRadius: 999,
  fontSize: 10,
  border: '1px solid',
  cursor: 'pointer',
};

function renderAssetCard({
  asset,
  isSelected,
  isPinned,
  onToggleAsset,
  onTogglePinnedAsset,
}: {
  asset: AssetCatalogItem;
  isSelected: boolean;
  isPinned: boolean;
  onToggleAsset: (assetKey: string) => void;
  onTogglePinnedAsset: (assetKey: string) => void;
}) {
  const metaBits = [
    asset.display_name && asset.display_name !== asset.symbol ? asset.display_name : null,
    asset.token_standards[0] ?? null,
    asset.observed_transfer_count > 0 ? `${asset.observed_transfer_count} hits` : null,
  ].filter((value): value is string => Boolean(value));
  const identity = IDENTITY_BADGE[asset.identity_status];
  const identityNote = asset.canonical_symbol
    ? `${asset.variant_kind} view of ${asset.canonical_symbol}`
    : asset.variant_kind;

  return (
    <div
      key={asset.asset_key}
      style={{
        display: 'grid',
        gridTemplateColumns: '1fr auto',
        gap: 8,
        alignItems: 'stretch',
      }}
    >
      <button
        onClick={() => onToggleAsset(asset.asset_key)}
        aria-pressed={isSelected}
        style={{
          borderRadius: 10,
          border: `1px solid ${isSelected ? '#14b8a6' : '#334155'}`,
          background: isSelected ? '#0f766e' : '#0f172a',
          color: isSelected ? '#f8fafc' : '#e2e8f0',
          cursor: 'pointer',
          padding: '10px 12px',
          textAlign: 'left',
          display: 'grid',
          gap: 6,
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'flex-start' }}>
          <div style={{ display: 'grid', gap: 3 }}>
            <span style={{ fontSize: 12, fontWeight: 700 }}>{asset.symbol}</span>
            <span style={{ fontSize: 10, color: isSelected ? '#ccfbf1' : '#94a3b8' }}>
              {identityNote}
            </span>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'flex-end', gap: 4 }}>
            <span
              style={{
                fontSize: 10,
                padding: '2px 6px',
                borderRadius: 999,
                border: `1px solid ${identity.border}`,
                color: isSelected ? '#f8fafc' : identity.color,
                background: isSelected ? 'rgba(15, 23, 42, 0.18)' : 'transparent',
              }}
            >
              {identity.label}
            </span>
            <span style={{ fontSize: 10, color: isSelected ? '#ccfbf1' : '#94a3b8' }}>
              {asset.is_native ? 'native' : asset.blockchains.join(' / ')}
            </span>
          </div>
        </div>
        {metaBits.length > 0 ? (
          <span style={{ fontSize: 10, color: isSelected ? '#ccfbf1' : '#94a3b8' }}>
            {metaBits.join(' | ')}
          </span>
        ) : null}
        {asset.sample_asset_address ? (
          <span style={{ fontSize: 10, color: isSelected ? '#ccfbf1' : '#64748b' }}>
            {asset.sample_asset_address}
          </span>
        ) : null}
      </button>
      <button
        type="button"
        onClick={() => onTogglePinnedAsset(asset.asset_key)}
        aria-pressed={isPinned}
        title={isPinned ? 'Unpin asset' : 'Pin asset'}
        style={{
          borderRadius: 10,
          border: `1px solid ${isPinned ? '#f59e0b' : '#334155'}`,
          background: isPinned ? 'rgba(245,158,11,0.12)' : '#020617',
          color: isPinned ? '#fbbf24' : '#94a3b8',
          cursor: 'pointer',
          padding: '0 10px',
          fontSize: 11,
          minWidth: 58,
          fontWeight: 700,
        }}
      >
        {isPinned ? 'Pinned' : 'Pin'}
      </button>
    </div>
  );
}
