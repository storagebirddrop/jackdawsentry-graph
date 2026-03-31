import { useEffect, useMemo, useState } from 'react';
import type { Edge, Node } from '@xyflow/react';

import type {
  ActivitySummary,
  AddressNodeData,
  BtcSidechainPegData,
  BridgeHopData,
  EntityNodeData,
  ExpansionResponseV2,
  InvestigationEdge,
  InvestigationNode,
  LightningChannelCloseData,
  LightningChannelOpenData,
  SwapEventData,
  UTXONodeData,
  AtomicSwapData,
} from '../types/graph';
import type { BranchMeta } from '../store/graphStore';
import type { BridgeStatusRefreshState } from '../hooks/useBridgeHopPoller';
import {
  bridgeAssetRouteLabel,
  bridgeMechanismLabel,
  bridgeProtocolLabel,
  bridgeRouteLabel,
  bridgeStatusTone,
  formatNative,
  formatTimestamp,
  formatUsd,
  getChainColor,
  GraphGlyph,
  glyphSurfaceStyle,
  type NodeSemanticMeta,
  nodeGlyphKind,
  riskColor,
  riskLabel,
  semanticBadges,
  semanticMetaForNode,
  shortHash,
} from './graphVisuals';

// Add CSS for accessible focus indicators
const styleSheet = typeof document !== 'undefined' ? (() => {
  const style = document.createElement('style');
  style.textContent = `
    .filter-input:focus-visible {
      outline: 2px solid #0f766e;
      outline-offset: 1px;
    }
  `;
  document.head.appendChild(style);
  return style;
})() : null;

export interface PathStory {
  pathId: string;
  lineageId: string;
  title: string;
  summary: string;
  nodeCount: number;
  minDepth: number;
  maxDepth: number;
  branchCount: number;
  chains: string[];
  color: string;
}

interface Props {
  node: Node | null;
  edge: Edge | null;
  collapsed: boolean;
  onClose: () => void;
  onToggleCollapsed: () => void;
  onFocusBridgeRoute?: (route: string) => void;
  onFocusBridgeProtocol?: (protocolId: string) => void;
  onClearBridgeFocus?: () => void;
  activeBridgeRoute?: string | null;
  activeBridgeProtocols?: string[];
  onFocusBranch?: (branchId: string) => void;
  onCompareBranch?: (branchId: string) => void;
  onClearBranchFocus?: () => void;
  activeBranchIds?: string[];
  branchMeta?: BranchMeta | null;
  onTogglePinnedPath?: (pathId: string) => void;
  onClearPinnedPaths?: () => void;
  pinnedPathIds?: string[];
  pinnedPaths?: PathStory[];
  pathStory?: PathStory | null;
  semanticMeta?: NodeSemanticMeta | null;
  semanticVisibleCount?: number;
  activeSemanticKey?: string | null;
  onFocusSemanticKey?: (key: string) => void;
  onClearSemanticFocus?: () => void;
  canTraceEdgeForward?: boolean;
  canTraceEdgeBackward?: boolean;
  onTraceEdgeForward?: () => void;
  onTraceEdgeBackward?: () => void;
  onExpandNode?: (operation: 'expand_prev' | 'expand_next' | 'expand_neighbors') => void;
  onPreviewExpand?: (
    operation: 'expand_prev' | 'expand_next' | 'expand_neighbors',
    filters: { timeFrom?: string; timeTo?: string; maxResults?: number },
  ) => void;
  previewResult?: ExpansionResponseV2 | null;
  onApplyPreview?: (selectedEdgeIds?: Set<string>) => void;
  onDismissPreview?: () => void;
  isPreviewLoading?: boolean;
  onHideNode?: (nodeId: string) => void;
  bridgeStatusRefresh?: BridgeStatusRefreshState | null;
}

export default function GraphInspectorPanel({
  node,
  edge,
  collapsed,
  onClose,
  onToggleCollapsed,
  onFocusBridgeRoute,
  onFocusBridgeProtocol,
  onClearBridgeFocus,
  activeBridgeRoute,
  activeBridgeProtocols = [],
  onFocusBranch,
  onCompareBranch,
  onClearBranchFocus,
  activeBranchIds = [],
  branchMeta = null,
  onTogglePinnedPath,
  onClearPinnedPaths,
  pinnedPathIds = [],
  pinnedPaths = [],
  pathStory = null,
  semanticMeta = null,
  semanticVisibleCount = 0,
  activeSemanticKey = null,
  onFocusSemanticKey,
  onClearSemanticFocus,
  canTraceEdgeForward = false,
  canTraceEdgeBackward = false,
  onTraceEdgeForward,
  onTraceEdgeBackward,
  onExpandNode,
  onPreviewExpand,
  previewResult = null,
  onApplyPreview,
  onDismissPreview,
  isPreviewLoading = false,
  onHideNode,
  bridgeStatusRefresh = null,
}: Props) {
  const selectedNode = useMemo(
    () => (node?.data as InvestigationNode | undefined) ?? null,
    [node],
  );
  const selectedEdge = useMemo(
    () => (edge?.data as InvestigationEdge | undefined) ?? null,
    [edge],
  );

  if (collapsed) {
    return (
      <aside style={collapsedPanelStyle}>
        <button
          onClick={onToggleCollapsed}
          style={collapseButtonStyle}
          aria-label="Expand inspector"
          title="Expand inspector"
        >
          {'<'}
        </button>
        <div style={collapsedLabelStyle}>Inspector</div>
        {(selectedNode || selectedEdge) && (
          <div style={collapsedMetaStyle}>
            {selectedNode ? selectedNode.node_type.replace(/_/g, ' ') : 'edge'}
          </div>
        )}
      </aside>
    );
  }

  return (
    <aside style={panelStyle}>
      <div style={headerStyle}>
        <div>
          <div style={eyebrowStyle}>Selection</div>
          <div style={titleStyle}>Inspector</div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            onClick={onToggleCollapsed}
            style={collapseButtonStyle}
            aria-label="Collapse inspector"
            title="Collapse inspector"
          >
            {'>'}
          </button>
          <button onClick={onClose} style={closeButtonStyle} aria-label="Close inspector">
            x
          </button>
        </div>
      </div>

      {!selectedNode && !selectedEdge ? (
        <EmptyState />
      ) : selectedNode ? (
        <NodeInspectorContent
          node={selectedNode}
          onFocusBridgeRoute={onFocusBridgeRoute}
          onFocusBridgeProtocol={onFocusBridgeProtocol}
          onClearBridgeFocus={onClearBridgeFocus}
          activeBridgeRoute={activeBridgeRoute}
          activeBridgeProtocols={activeBridgeProtocols}
          onFocusBranch={onFocusBranch}
          onCompareBranch={onCompareBranch}
          onClearBranchFocus={onClearBranchFocus}
          activeBranchIds={activeBranchIds}
          branchMeta={branchMeta}
          onTogglePinnedPath={onTogglePinnedPath}
          onClearPinnedPaths={onClearPinnedPaths}
          pinnedPathIds={pinnedPathIds}
          pinnedPaths={pinnedPaths}
          pathStory={pathStory}
          semanticMeta={semanticMeta}
          semanticVisibleCount={semanticVisibleCount}
          activeSemanticKey={activeSemanticKey}
          onFocusSemanticKey={onFocusSemanticKey}
          onClearSemanticFocus={onClearSemanticFocus}
          onExpandNode={onExpandNode}
          onPreviewExpand={onPreviewExpand}
          previewResult={previewResult}
          onApplyPreview={onApplyPreview}
          onDismissPreview={onDismissPreview}
          isPreviewLoading={isPreviewLoading}
          onHideNode={onHideNode}
          bridgeStatusRefresh={bridgeStatusRefresh}
        />
      ) : selectedEdge ? (
        <EdgeInspectorContent
          edge={selectedEdge}
          canTraceForward={canTraceEdgeForward}
          canTraceBackward={canTraceEdgeBackward}
          onTraceForward={onTraceEdgeForward}
          onTraceBackward={onTraceEdgeBackward}
        />
      ) : null}
    </aside>
  );
}

function EmptyState() {
  return (
    <div style={{ marginTop: 20, display: 'grid', gap: 14 }}>
      <div style={emptyCardStyle}>
        <div style={emptyTitleStyle}>Select a node or edge</div>
        <div style={emptyBodyStyle}>
          The canvas now supports a richer investigator shell. Click any node to inspect
          risk, entity context, values, routing details, and semantic badges.
        </div>
      </div>
      <div style={emptyCardStyle}>
        <div style={emptyTitleStyle}>Current UX pass</div>
        <div style={emptyBodyStyle}>
          This panel is the new home for node detail and transaction context. Manual
          clustering and persistent analyst comments still need dedicated backend flows.
        </div>
      </div>
    </div>
  );
}

function NodeInspectorContent({
  node,
  onFocusBridgeRoute,
  onFocusBridgeProtocol,
  onClearBridgeFocus,
  activeBridgeRoute,
  activeBridgeProtocols,
  onFocusBranch,
  onCompareBranch,
  onClearBranchFocus,
  activeBranchIds,
  branchMeta,
  onTogglePinnedPath,
  onClearPinnedPaths,
  pinnedPathIds,
  pinnedPaths,
  pathStory,
  semanticMeta,
  semanticVisibleCount,
  activeSemanticKey,
  onFocusSemanticKey,
  onClearSemanticFocus,
  onExpandNode,
  onPreviewExpand,
  previewResult,
  onApplyPreview,
  onDismissPreview,
  isPreviewLoading = false,
  onHideNode,
  bridgeStatusRefresh,
}: {
  node: InvestigationNode;
  onFocusBridgeRoute?: (route: string) => void;
  onFocusBridgeProtocol?: (protocolId: string) => void;
  onClearBridgeFocus?: () => void;
  activeBridgeRoute?: string | null;
  activeBridgeProtocols: string[];
  onFocusBranch?: (branchId: string) => void;
  onCompareBranch?: (branchId: string) => void;
  onClearBranchFocus?: () => void;
  activeBranchIds: string[];
  branchMeta: BranchMeta | null;
  onTogglePinnedPath?: (pathId: string) => void;
  onClearPinnedPaths?: () => void;
  pinnedPathIds: string[];
  pinnedPaths: PathStory[];
  pathStory: PathStory | null;
  semanticMeta: NodeSemanticMeta | null;
  semanticVisibleCount: number;
  activeSemanticKey: string | null;
  onFocusSemanticKey?: (key: string) => void;
  onClearSemanticFocus?: () => void;
  onExpandNode?: (operation: 'expand_prev' | 'expand_next' | 'expand_neighbors') => void;
  onPreviewExpand?: (
    operation: 'expand_prev' | 'expand_next' | 'expand_neighbors',
    filters: { timeFrom?: string; timeTo?: string; maxResults?: number },
  ) => void;
  previewResult?: ExpansionResponseV2 | null;
  onApplyPreview?: (selectedEdgeIds?: Set<string>) => void;
  onDismissPreview?: () => void;
  isPreviewLoading?: boolean;
  onHideNode?: (nodeId: string) => void;
  bridgeStatusRefresh?: BridgeStatusRefreshState | null;
}) {
  const accent = getChainColor(node.chain ?? (node.address_data as AddressNodeData | undefined)?.chain);
  const badges = semanticBadges(node);
  const title = nodeTitle(node);
  const subtitle = nodeSubtitle(node);
  const activity = node.activity_summary;
  const nodeSemantic = semanticMeta ?? semanticMetaForNode(node);

  // Filter & Preview local state
  const [showFilters, setShowFilters] = useState(false);
  const [filterTimeFrom, setFilterTimeFrom] = useState('');
  const [filterTimeTo, setFilterTimeTo] = useState('');
  const [filterMaxResults, setFilterMaxResults] = useState(25);
  const [previewDirection, setPreviewDirection] = useState<'expand_prev' | 'expand_next' | 'expand_neighbors'>('expand_next');
  const [selectedEdgeIds, setSelectedEdgeIds] = useState<Set<string>>(new Set());
  useEffect(() => {
    const edges = previewResult?.edges ?? previewResult?.added_edges ?? [];
    setSelectedEdgeIds(new Set(edges.map((e) => e.edge_id)));
  }, [previewResult]);

  return (
    <div style={{ display: 'grid', gap: 18, marginTop: 18 }}>
      <div style={heroCardStyle}>
        <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
          <div style={glyphSurfaceStyle(accent)}>
            <GraphGlyph kind={nodeGlyphKind(node)} accent={accent} />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={titleBadgeStyle}>{node.node_type.replace(/_/g, ' ')}</div>
            <div style={heroTitleStyle}>{title}</div>
            {subtitle && <div style={heroSubtitleStyle}>{subtitle}</div>}
          </div>
        </div>
        {badges.length > 0 && (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 14 }}>
            {badges.map((badge) => (
              <span key={`${badge.label}-${badge.tone}`} style={{
                padding: '4px 9px',
                borderRadius: 999,
                background: `${badge.tone}18`,
                border: `1px solid ${badge.tone}38`,
                color: badge.tone,
                fontWeight: 700,
                fontSize: 11,
              }}>
                {badge.label}
              </span>
            ))}
          </div>
        )}
      </div>

      {activity && <ActivitySection summary={activity} />}

      {node.node_type === 'address' && <AddressSection node={node} />}
      {(node.node_type === 'entity' || node.node_type === 'service') && <EntitySection node={node} />}
      {node.node_type === 'swap_event' && <SwapSection node={node} />}
      {node.node_type === 'bridge_hop' && (
        <BridgeSection
          node={node}
          onFocusBridgeRoute={onFocusBridgeRoute}
          onFocusBridgeProtocol={onFocusBridgeProtocol}
          onClearBridgeFocus={onClearBridgeFocus}
          activeBridgeRoute={activeBridgeRoute}
          activeBridgeProtocols={activeBridgeProtocols}
          bridgeStatusRefresh={bridgeStatusRefresh}
        />
      )}
      {node.node_type === 'lightning_channel_open' && <LightningChannelOpenSection node={node} />}
      {node.node_type === 'lightning_channel_close' && <LightningChannelCloseSection node={node} />}
      {(node.node_type === 'btc_sidechain_peg_in' || node.node_type === 'btc_sidechain_peg_out') && (
        <BtcSidechainPegSection node={node} />
      )}
      {node.node_type === 'atomic_swap' && <AtomicSwapSection node={node} />}
      {node.node_type === 'utxo' && <UtxoSection node={node} />}

      {nodeSemantic && (
        <Section title="Protocol context">
          <KeyValue label="Semantic rail">{nodeSemantic.label}</KeyValue>
          <KeyValue label="Family">{nodeSemantic.family}</KeyValue>
          <KeyValue label="Visible count">{semanticVisibleCount}</KeyValue>
          {(onFocusSemanticKey || onClearSemanticFocus) && (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
              {onFocusSemanticKey && (
                <button
                  type="button"
                  onClick={() => onFocusSemanticKey(nodeSemantic.key)}
                  style={{
                    ...actionButtonStyle,
                    color: activeSemanticKey === nodeSemantic.key ? nodeSemantic.color : '#334155',
                    borderColor: activeSemanticKey === nodeSemantic.key
                      ? `${nodeSemantic.color}55`
                      : 'rgba(148, 163, 184, 0.3)',
                  }}
                >
                  {activeSemanticKey === nodeSemantic.key ? 'Protocol focused' : 'Focus protocol'}
                </button>
              )}
              {onClearSemanticFocus && activeSemanticKey && (
                <button type="button" onClick={onClearSemanticFocus} style={actionButtonStyle}>
                  Clear protocol focus
                </button>
              )}
            </div>
          )}
        </Section>
      )}

      <Section title="Workspace actions">
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {node.expandable_directions.includes('prev') && onExpandNode && (
            <button type="button" onClick={() => onExpandNode('expand_prev')} style={actionButtonStyle}>
              Expand previous
            </button>
          )}
          {node.expandable_directions.includes('next') && onExpandNode && (
            <button type="button" onClick={() => onExpandNode('expand_next')} style={actionButtonStyle}>
              Expand next
            </button>
          )}
          {node.expandable_directions.includes('neighbors') && onExpandNode && (
            <button type="button" onClick={() => onExpandNode('expand_neighbors')} style={actionButtonStyle}>
              Expand around
            </button>
          )}
          {onPreviewExpand && (
            <button
              type="button"
              onClick={() => setShowFilters((v) => !v)}
              style={{
                ...actionButtonStyle,
                color: showFilters ? '#0f766e' : '#334155',
                borderColor: showFilters ? 'rgba(15,118,110,0.35)' : 'rgba(148,163,184,0.3)',
              }}
            >
              {showFilters ? 'Hide filters ▲' : 'Filter & Preview ▼'}
            </button>
          )}
          {onHideNode && (
            <button
              type="button"
              onClick={() => onHideNode(node.node_id)}
              style={{
                ...actionButtonStyle,
                color: '#b91c1c',
                borderColor: 'rgba(239,68,68,0.26)',
              }}
            >
              Remove from canvas
            </button>
          )}
        </div>

        {showFilters && onPreviewExpand && (
          <div style={{ marginTop: 12, display: 'grid', gap: 8 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              <label style={filterLabelStyle}>
                From
                <input
                  type="date"
                  value={filterTimeFrom}
                  onChange={(e) => setFilterTimeFrom(e.target.value)}
                  className="filter-input"
                  style={filterInputStyle}
                />
              </label>
              <label style={filterLabelStyle}>
                To
                <input
                  type="date"
                  value={filterTimeTo}
                  onChange={(e) => setFilterTimeTo(e.target.value)}
                  className="filter-input"
                  style={filterInputStyle}
                />
              </label>
            </div>
            <label style={filterLabelStyle}>
              Max results
              <input
                type="number"
                min={1}
                max={100}
                value={filterMaxResults}
                onChange={(e) => setFilterMaxResults(Math.max(1, Math.min(100, Number(e.target.value))))}
                className="filter-input"
                style={{ ...filterInputStyle, width: 72 }}
              />
            </label>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 2 }}>
              {node.expandable_directions.includes('prev') && (
                <button
                  type="button"
                  disabled={isPreviewLoading}
                  onClick={() => {
                    setPreviewDirection('expand_prev');
                    onPreviewExpand('expand_prev', {
                      timeFrom: filterTimeFrom || undefined,
                      timeTo: filterTimeTo || undefined,
                      maxResults: filterMaxResults,
                    });
                  }}
                  style={{ ...actionButtonStyle, opacity: isPreviewLoading ? 0.6 : 1 }}
                >
                  {isPreviewLoading && previewDirection === 'expand_prev' ? 'Loading…' : 'Preview prev'}
                </button>
              )}
              {node.expandable_directions.includes('next') && (
                <button
                  type="button"
                  disabled={isPreviewLoading}
                  onClick={() => {
                    setPreviewDirection('expand_next');
                    onPreviewExpand('expand_next', {
                      timeFrom: filterTimeFrom || undefined,
                      timeTo: filterTimeTo || undefined,
                      maxResults: filterMaxResults,
                    });
                  }}
                  style={{ ...actionButtonStyle, opacity: isPreviewLoading ? 0.6 : 1 }}
                >
                  {isPreviewLoading && previewDirection === 'expand_next' ? 'Loading…' : 'Preview next'}
                </button>
              )}
              {node.expandable_directions.includes('neighbors') && (
                <button
                  type="button"
                  disabled={isPreviewLoading}
                  onClick={() => {
                    setPreviewDirection('expand_neighbors');
                    onPreviewExpand('expand_neighbors', {
                      timeFrom: filterTimeFrom || undefined,
                      timeTo: filterTimeTo || undefined,
                      maxResults: filterMaxResults,
                    });
                  }}
                  style={{ ...actionButtonStyle, opacity: isPreviewLoading ? 0.6 : 1 }}
                >
                  {isPreviewLoading && previewDirection === 'expand_neighbors' ? 'Loading…' : 'Preview around'}
                </button>
              )}
            </div>

            {previewResult && (() => {
              const candidateEdges = previewResult.edges ?? previewResult.added_edges ?? [];
              const isBitcoin = (previewResult.chain_context?.primary_chain ?? '') === 'bitcoin';
              const hasMore = previewResult.pagination?.has_more ?? false;
              const unit = isBitcoin ? 'UTXO output' : 'transfer';
              const seedId = previewResult.seed_node_id;
              return (
                <div style={previewResultStyle}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                    <div style={{ fontWeight: 600, fontSize: 12, color: '#0f766e' }}>
                      {hasMore ? `${candidateEdges.length}+` : candidateEdges.length}{' '}
                      {unit}{(candidateEdges.length !== 1 || hasMore) ? 's' : ''} found
                    </div>
                    {candidateEdges.length > 0 && (
                      <div style={{ display: 'flex', gap: 4 }}>
                        <button
                          type="button"
                          onClick={() => setSelectedEdgeIds(new Set(candidateEdges.map((e) => e.edge_id)))}
                          style={{ fontSize: 10, padding: '1px 6px', borderRadius: 4, border: '1px solid rgba(148,163,184,0.4)', background: 'transparent', cursor: 'pointer', color: '#64748b' }}
                        >
                          All
                        </button>
                        <button
                          type="button"
                          onClick={() => setSelectedEdgeIds(new Set())}
                          style={{ fontSize: 10, padding: '1px 6px', borderRadius: 4, border: '1px solid rgba(148,163,184,0.4)', background: 'transparent', cursor: 'pointer', color: '#64748b' }}
                        >
                          None
                        </button>
                      </div>
                    )}
                  </div>
                  {hasMore && (
                    <div style={{ fontSize: 11, color: '#92400e', marginBottom: 4 }}>
                      Showing first {candidateEdges.length} — narrow the date range or reduce max results.
                    </div>
                  )}
                  {previewResult.integrity_warning && (
                    <div style={{ fontSize: 11, color: '#92400e', marginBottom: 6 }}>
                      ⚠ {previewResult.integrity_warning}
                    </div>
                  )}
                  <div style={{ maxHeight: 220, overflowY: 'auto', display: 'grid', gap: 2, marginBottom: 8 }}>
                    {candidateEdges.map((edge) => {
                      const checked = selectedEdgeIds.has(edge.edge_id);
                      const counterpartyId = seedId !== undefined
                        ? (edge.source_node_id === seedId
                            ? edge.target_node_id
                            : edge.source_node_id)
                        : (edge.source_node_id === edge.target_node_id
                            ? edge.source_node_id
                            : `Unknown (seed: ${edge.source_node_id} ↔ ${edge.target_node_id})`);
                      const addrPart = counterpartyId.split(':').pop() ?? counterpartyId;
                      const displayAddr = shortHash(addrPart, 8, 4);
                      return (
                        <label
                          key={edge.edge_id}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 6,
                            padding: '4px 6px',
                            borderRadius: 5,
                            background: checked ? 'rgba(15,118,110,0.07)' : 'transparent',
                            cursor: 'pointer',
                            fontSize: 11,
                            color: '#334155',
                          }}
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={(ev) => {
                              const next = new Set(selectedEdgeIds);
                              if (ev.target.checked) next.add(edge.edge_id);
                              else next.delete(edge.edge_id);
                              setSelectedEdgeIds(next);
                            }}
                            style={{ flexShrink: 0 }}
                          />
                          <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {displayAddr}
                          </span>
                          {edge.value_native != null && (
                            <span style={{ color: '#0f766e', flexShrink: 0, fontSize: 10 }}>
                              {formatNative(edge.value_native, edge.asset_symbol)}
                            </span>
                          )}
                          {edge.timestamp && (
                            <span style={{ color: '#94a3b8', flexShrink: 0, fontSize: 10 }}>
                              {formatTimestamp(edge.timestamp)}
                            </span>
                          )}
                          {edge.is_suspected_change && (
                            <span title="Probable change output" style={{ color: '#7c3aed', flexShrink: 0 }}>⟲</span>
                          )}
                        </label>
                      );
                    })}
                  </div>
                  <div style={{ fontSize: 11, color: '#64748b', marginBottom: 6 }}>
                    {selectedEdgeIds.size} of {candidateEdges.length} selected
                  </div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {onApplyPreview && (
                      <button
                        type="button"
                        disabled={selectedEdgeIds.size === 0}
                        onClick={() => onApplyPreview(selectedEdgeIds)}
                        style={{ ...applyButtonStyle, opacity: selectedEdgeIds.size === 0 ? 0.5 : 1 }}
                      >
                        Apply selected ({selectedEdgeIds.size})
                      </button>
                    )}
                    {onApplyPreview && (
                      <button type="button" onClick={() => onApplyPreview(undefined)} style={actionButtonStyle}>
                        Apply all
                      </button>
                    )}
                    {onDismissPreview && (
                      <button type="button" onClick={onDismissPreview} style={actionButtonStyle}>
                        Dismiss
                      </button>
                    )}
                  </div>
                </div>
              );
            })()}
          </div>
        )}
      </Section>

      <Section title="Graph lineage">
        <KeyValue label="Branch">{shortHash(node.branch_id, 10, 6)}</KeyValue>
        {branchMeta && (
          <>
            <KeyValue label="Branch size">{branchMeta.nodeCount} nodes</KeyValue>
            <KeyValue label="Depth span">
              {branchMeta.minDepth}
              {' -> '}
              {branchMeta.maxDepth}
            </KeyValue>
          </>
        )}
        <KeyValue label="Path">{shortHash(node.path_id, 10, 6)}</KeyValue>
        <KeyValue label="Lineage">{shortHash(node.lineage_id, 10, 6)}</KeyValue>
        <KeyValue label="Depth">{node.depth}</KeyValue>
        <KeyValue label="Expandability">
          {node.expandable_directions.length > 0
            ? node.expandable_directions.join(', ')
            : 'Not expandable'}
        </KeyValue>
        {(onFocusBranch || onCompareBranch || onClearBranchFocus) && (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
            {onFocusBranch && (
              <button
                type="button"
                onClick={() => onFocusBranch(node.branch_id)}
                style={{
                  ...actionButtonStyle,
                  color:
                    activeBranchIds.length === 1 && activeBranchIds[0] === node.branch_id
                      ? '#2563eb'
                      : '#334155',
                  borderColor:
                    activeBranchIds.length === 1 && activeBranchIds[0] === node.branch_id
                      ? 'rgba(37,99,235,0.34)'
                      : 'rgba(148,163,184,0.3)',
                }}
              >
                {activeBranchIds.length === 1 && activeBranchIds[0] === node.branch_id
                  ? 'Branch focused'
                  : 'Focus branch'}
              </button>
            )}
            {onCompareBranch && (
              <button
                type="button"
                onClick={() => onCompareBranch(node.branch_id)}
                style={{
                  ...actionButtonStyle,
                  color: activeBranchIds.includes(node.branch_id) && activeBranchIds.length > 1
                    ? '#7c3aed'
                    : '#334155',
                  borderColor: activeBranchIds.includes(node.branch_id) && activeBranchIds.length > 1
                    ? 'rgba(124,58,237,0.34)'
                    : 'rgba(148,163,184,0.3)',
                }}
              >
                {activeBranchIds.includes(node.branch_id) && activeBranchIds.length > 1
                  ? 'Compared'
                  : 'Compare branch'}
              </button>
            )}
            {onClearBranchFocus && activeBranchIds.length > 0 && (
              <button
                type="button"
                onClick={onClearBranchFocus}
                style={actionButtonStyle}
              >
                Clear branch focus
              </button>
            )}
          </div>
        )}
      </Section>

      <Section title="Pinned path story">
        <KeyValue label="Path">{shortHash(node.path_id, 10, 6)}</KeyValue>
        <KeyValue label="Lineage">{shortHash(node.lineage_id, 10, 6)}</KeyValue>
        {pathStory && (
          <>
            <KeyValue label="Story arc">{pathStory.summary}</KeyValue>
            <KeyValue label="Coverage">
              {pathStory.nodeCount} nodes
              {' · depth '}
              {pathStory.minDepth}
              {'-'}
              {pathStory.maxDepth}
            </KeyValue>
            <KeyValue label="Branches">{pathStory.branchCount}</KeyValue>
            <KeyValue label="Chains">{pathStory.chains.join(' -> ') || 'mixed'}</KeyValue>
          </>
        )}
        {(onTogglePinnedPath || onClearPinnedPaths) && (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
            {onTogglePinnedPath && (
              <button
                type="button"
                onClick={() => onTogglePinnedPath(node.path_id)}
                style={{
                  ...actionButtonStyle,
                  color: pinnedPathIds.includes(node.path_id) ? '#b45309' : '#334155',
                  borderColor: pinnedPathIds.includes(node.path_id)
                    ? 'rgba(180,83,9,0.34)'
                    : 'rgba(148,163,184,0.3)',
                }}
              >
                {pinnedPathIds.includes(node.path_id) ? 'Unpin path' : 'Pin path'}
              </button>
            )}
            {onClearPinnedPaths && pinnedPathIds.length > 0 && (
              <button
                type="button"
                onClick={onClearPinnedPaths}
                style={actionButtonStyle}
              >
                Clear pinned paths
              </button>
            )}
          </div>
        )}
        {pinnedPaths.length > 0 && (
          <div style={{ display: 'grid', gap: 10, marginTop: 12 }}>
            {pinnedPaths.map((story) => {
              const current = story.pathId === node.path_id;
              return (
                <div
                  key={story.pathId}
                  style={pathStoryCardStyle(story.color, current)}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'flex-start' }}>
                    <div>
                      <div style={pathStoryEyebrowStyle(story.color)}>
                        {current ? 'Current pinned path' : 'Pinned path'}
                      </div>
                      <div style={pathStoryTitleStyle}>{story.title}</div>
                      <div style={pathStorySubtitleStyle}>{story.summary}</div>
                    </div>
                    {onTogglePinnedPath && (
                      <button
                        type="button"
                        onClick={() => onTogglePinnedPath(story.pathId)}
                        style={{
                          ...actionButtonStyle,
                          padding: '6px 10px',
                          fontSize: 11,
                        }}
                      >
                        Unpin
                      </button>
                    )}
                  </div>
                  <div style={pathStoryMetaStyle}>
                    {story.nodeCount} nodes
                    {' · depth '}
                    {story.minDepth}
                    {'-'}
                    {story.maxDepth}
                    {' · '}
                    {story.branchCount} branches
                  </div>
                  <div style={pathStoryMetaStyle}>
                    {story.chains.join(' -> ') || 'mixed chains'}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Section>
    </div>
  );
}

function AddressSection({ node }: { node: InvestigationNode }) {
  const address = (node.address_data ?? node.node_data) as AddressNodeData;
  const chain = address.chain ?? node.chain;

  return (
    <Section title="Address profile">
      <KeyValue label="Address">
        <code style={codeStyle}>{address.address}</code>
      </KeyValue>
      <KeyValue label="Chain">{chain}</KeyValue>
      <KeyValue label="Attribution">{node.entity_name ?? address.entity_name ?? 'Unattributed'}</KeyValue>
      <KeyValue label="Category">{node.entity_category ?? address.entity_category ?? 'Unknown'}</KeyValue>
      <KeyValue label="Risk">
        <span style={{ color: riskColor(node.risk_score ?? address.risk_score), fontWeight: 700 }}>
          {riskLabel(node.risk_score ?? address.risk_score)}
          {(node.risk_score ?? address.risk_score) !== undefined && ` (${Math.round((node.risk_score ?? address.risk_score ?? 0) * 100)}%)`}
        </span>
      </KeyValue>
      <KeyValue label="Observed fiat">
        {formatUsd(node.balance_fiat ?? address.fiat_value_usd) ?? 'Not available'}
      </KeyValue>
    </Section>
  );
}

function EntitySection({ node }: { node: InvestigationNode }) {
  const entity = node.node_data as EntityNodeData & { service_type?: string; display_name?: string };
  const activity = node.activity_summary;

  if (node.node_type === 'service' && activity) {
    return (
      <Section title="Activity profile">
        <KeyValue label="Title">{activity.title}</KeyValue>
        <KeyValue label="Protocol">{activity.protocol_id ?? 'Unknown'}</KeyValue>
        <KeyValue label="Type">{activity.protocol_type ?? activity.activity_type}</KeyValue>
        <KeyValue label="Contract">
          {activity.contract_address ? <code style={codeStyle}>{activity.contract_address}</code> : 'Unknown'}
        </KeyValue>
        <KeyValue label="Direction">{activity.direction ?? 'Unknown'}</KeyValue>
        <KeyValue label="Asset">{activity.asset_symbol ?? 'Unknown'}</KeyValue>
        <KeyValue label="Value">{formatNative(activity.value_native, activity.asset_symbol) ?? 'Unknown'}</KeyValue>
        <KeyValue label="Fiat">{formatUsd(activity.value_fiat) ?? 'Unknown'}</KeyValue>
      </Section>
    );
  }

  return (
    <Section title="Entity profile">
      <KeyValue label="Name">{entity.name ?? entity.display_name ?? 'Unknown entity'}</KeyValue>
      <KeyValue label="Category">{entity.category ?? entity.service_type ?? 'Unknown'}</KeyValue>
      <KeyValue label="Addresses">
        {'address_count' in entity && entity.address_count !== undefined ? entity.address_count : 'Unknown'}
      </KeyValue>
      <KeyValue label="Jurisdiction">{entity.jurisdiction ?? 'Unknown'}</KeyValue>
      <KeyValue label="Risk">
        <span style={{ color: riskColor(entity.risk_score), fontWeight: 700 }}>
          {riskLabel(entity.risk_score)}
          {entity.risk_score !== undefined && ` (${Math.round(entity.risk_score * 100)}%)`}
        </span>
      </KeyValue>
    </Section>
  );
}

function SwapSection({ node }: { node: InvestigationNode }) {
  const swap = (node.swap_event_data ?? node.node_data) as SwapEventData | undefined;
  if (!swap) return null;

  return (
    <Section title="Swap details">
      <KeyValue label="Protocol">{swap.protocol_id}</KeyValue>
      <KeyValue label="Chain">{swap.chain}</KeyValue>
      <KeyValue label="Route">{`${swap.input_asset} -> ${swap.output_asset}`}</KeyValue>
      <KeyValue label="Input">{formatNative(swap.input_amount, swap.input_asset) ?? 'Unknown'}</KeyValue>
      <KeyValue label="Output">{formatNative(swap.output_amount, swap.output_asset) ?? 'Unknown'}</KeyValue>
      <KeyValue label="Rate">{swap.exchange_rate?.toFixed(6) ?? 'Unknown'}</KeyValue>
    </Section>
  );
}

function BridgeSection({
  node,
  onFocusBridgeRoute,
  onFocusBridgeProtocol,
  onClearBridgeFocus,
  activeBridgeRoute,
  activeBridgeProtocols,
  bridgeStatusRefresh,
}: {
  node: InvestigationNode;
  onFocusBridgeRoute?: (route: string) => void;
  onFocusBridgeProtocol?: (protocolId: string) => void;
  onClearBridgeFocus?: () => void;
  activeBridgeRoute?: string | null;
  activeBridgeProtocols: string[];
  bridgeStatusRefresh?: BridgeStatusRefreshState | null;
}) {
  const hop = node.node_data as BridgeHopData & {
    dest_chain?: string;
    dest_asset?: string;
    correlation_conf?: number;
  };
  const activity = node.activity_summary;
  const destinationChain = hop.destination_chain ?? hop.dest_chain;
  const destinationAddress = hop.destination_address ?? activity?.destination_address;
  const destinationAsset = hop.destination_asset ?? hop.dest_asset;
  const confidence = hop.correlation_confidence ?? hop.correlation_conf;
  const unresolvedCorrelation =
    hop.status === 'pending'
    || destinationChain == null
    || activity?.destination_tx_hash == null;
  const confidenceLabel = unresolvedCorrelation ? 'Correlation confidence' : 'Confidence';
  const protocolLabel = bridgeProtocolLabel(hop.protocol_id);
  const statusTone = bridgeStatusTone(hop.status);
  const routeLabel = bridgeRouteLabel({
    source_chain: hop.source_chain,
    destination_chain: destinationChain,
  });
  const protocolId = hop.protocol_id ?? 'unknown';
  const routeActive = activeBridgeRoute === routeLabel;
  const protocolActive = activeBridgeProtocols.includes(protocolId);

  return (
    <Section title="Bridge hop">
      <KeyValue label="Protocol">
        <span style={{ color: statusTone, fontWeight: 700 }}>{protocolLabel}</span>
      </KeyValue>
      <KeyValue label="Status">
        <span style={{ color: statusTone, fontWeight: 700, textTransform: 'uppercase' }}>
          {hop.status}
        </span>
      </KeyValue>
      {hop.status === 'pending' && (
        <KeyValue label="Refresh">
          {bridgeStatusRefresh?.isPolling ? 'Polling every 30s' : 'Refresh inactive'}
        </KeyValue>
      )}
      {bridgeStatusRefresh?.lastCheckedAt && (
        <KeyValue label="Last checked">
          {formatTimestamp(bridgeStatusRefresh.lastCheckedAt)}
        </KeyValue>
      )}
      <KeyValue label="Mechanism">{bridgeMechanismLabel(hop.mechanism)}</KeyValue>
      <KeyValue label="Route">
        {routeLabel}
      </KeyValue>
      {destinationAddress && (
        <KeyValue label="Destination">
          <code style={codeStyle}>{destinationAddress}</code>
        </KeyValue>
      )}
      <KeyValue label="Assets">
        {bridgeAssetRouteLabel({
          source_asset: hop.source_asset,
          destination_asset: destinationAsset,
          destination_chain: destinationChain,
        })}
      </KeyValue>
      <KeyValue label="Amounts">
        {hop.source_amount !== undefined
          ? formatNative(hop.source_amount, hop.source_asset) ?? 'Unknown'
          : 'Unknown'}
        {' -> '}
        {hop.destination_amount !== undefined && hop.destination_amount !== null
          ? formatNative(hop.destination_amount, destinationAsset) ?? 'Unknown'
          : 'Pending resolution'}
      </KeyValue>
      <KeyValue label={confidenceLabel}>{confidence !== undefined ? `${Math.round(confidence * 100)}%` : 'Unknown'}</KeyValue>
      <KeyValue label="Same asset">
        {hop.is_same_asset === undefined ? 'Unknown' : hop.is_same_asset ? 'Yes' : 'No'}
      </KeyValue>
      <KeyValue label="Time delta">
        {hop.time_delta_seconds !== undefined ? `${hop.time_delta_seconds}s` : 'Pending resolution'}
      </KeyValue>
      <KeyValue label="Hop ID"><code style={codeStyle}>{(hop as { hop_id?: string }).hop_id ?? node.node_id}</code></KeyValue>
      {activity?.source_tx_hash && (
        <KeyValue label="Source TX"><code style={codeStyle}>{activity.source_tx_hash}</code></KeyValue>
      )}
      {activity?.destination_tx_hash && (
        <KeyValue label="Dest TX"><code style={codeStyle}>{activity.destination_tx_hash}</code></KeyValue>
      )}
      {activity?.order_id && (
        <KeyValue label="Order ID"><code style={codeStyle}>{activity.order_id}</code></KeyValue>
      )}
      {activity?.route_summary && (
        <KeyValue label="Summary">{activity.route_summary}</KeyValue>
      )}
      {(onFocusBridgeRoute || onFocusBridgeProtocol || onClearBridgeFocus) && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
          {onFocusBridgeRoute && (
            <button
              type="button"
              onClick={() => onFocusBridgeRoute(routeLabel)}
              style={{
                ...actionButtonStyle,
                background: routeActive ? 'rgba(124,58,237,0.14)' : 'rgba(255,255,255,0.92)',
                borderColor: routeActive ? 'rgba(124,58,237,0.4)' : 'rgba(148,163,184,0.3)',
                color: routeActive ? '#7c3aed' : '#334155',
              }}
            >
              {routeActive ? 'Route focused' : 'Focus route'}
            </button>
          )}
          {onFocusBridgeProtocol && (
            <button
              type="button"
              onClick={() => onFocusBridgeProtocol(protocolId)}
              style={{
                ...actionButtonStyle,
                background: protocolActive ? 'rgba(37,99,235,0.14)' : 'rgba(255,255,255,0.92)',
                borderColor: protocolActive ? 'rgba(37,99,235,0.36)' : 'rgba(148,163,184,0.3)',
                color: protocolActive ? '#1d4ed8' : '#334155',
              }}
            >
              {protocolActive ? 'Protocol focused' : 'Focus protocol'}
            </button>
          )}
          {onClearBridgeFocus && (routeActive || protocolActive || activeBridgeRoute || activeBridgeProtocols.length > 0) && (
            <button
              type="button"
              onClick={onClearBridgeFocus}
              style={{
                ...actionButtonStyle,
                color: '#475569',
              }}
            >
              Clear bridge focus
            </button>
          )}
        </div>
      )}
      {bridgeStatusRefresh?.errorMessage && (
        <div
          style={{
            marginTop: 10,
            padding: '10px 12px',
            borderRadius: 12,
            border: '1px solid rgba(239, 68, 68, 0.22)',
            background: 'rgba(254, 242, 242, 0.88)',
            color: '#991b1b',
            fontSize: 12,
            lineHeight: 1.45,
          }}
        >
          {bridgeStatusRefresh.errorMessage}
        </div>
      )}
    </Section>
  );
}

function LightningChannelOpenSection({ node }: { node: InvestigationNode }) {
  const channel = (node.lightning_channel_open_data ?? node.node_data) as
    | LightningChannelOpenData
    | undefined;
  if (!channel) return null;

  return (
    <Section title="Lightning channel open">
      <KeyValue label="Channel ID"><code style={codeStyle}>{channel.channel_id}</code></KeyValue>
      {channel.short_channel_id && (
        <KeyValue label="Short channel ID"><code style={codeStyle}>{channel.short_channel_id}</code></KeyValue>
      )}
      <KeyValue label="Funding TX"><code style={codeStyle}>{channel.funding_tx_hash}</code></KeyValue>
      <KeyValue label="Funding Vout">{channel.funding_vout ?? 'Unknown'}</KeyValue>
      <KeyValue label="Capacity">{formatNative(channel.capacity_btc, 'BTC') ?? 'Unknown'}</KeyValue>
      <KeyValue label="Local peer">{channel.local_alias ?? channel.local_pubkey ?? 'Unknown'}</KeyValue>
      <KeyValue label="Remote peer">{channel.remote_alias ?? channel.remote_pubkey ?? 'Unknown'}</KeyValue>
      <KeyValue label="Visibility">{channel.is_private === undefined ? 'Unknown' : channel.is_private ? 'Private' : 'Public'}</KeyValue>
      <KeyValue label="Status">{channel.status ?? 'open'}</KeyValue>
    </Section>
  );
}

function BtcSidechainPegSection({ node }: { node: InvestigationNode }) {
  const peg = (node.btc_sidechain_peg_data ?? node.node_data) as
    | BtcSidechainPegData
    | undefined;
  if (!peg) return null;

  return (
    <Section title="Bitcoin sidechain peg">
      <KeyValue label="Sidechain">{peg.sidechain}</KeyValue>
      <KeyValue label="Asset flow">{`${peg.asset_in} -> ${peg.asset_out}`}</KeyValue>
      <KeyValue label="Bitcoin TX">
        {peg.bitcoin_tx_hash ? <code style={codeStyle}>{peg.bitcoin_tx_hash}</code> : 'Unknown'}
      </KeyValue>
      <KeyValue label="Sidechain TX">
        {peg.sidechain_tx_hash ? <code style={codeStyle}>{peg.sidechain_tx_hash}</code> : 'Unknown'}
      </KeyValue>
      <KeyValue label="Peg address / contract">
        {peg.peg_address_or_contract ? <code style={codeStyle}>{peg.peg_address_or_contract}</code> : 'Unknown'}
      </KeyValue>
      <KeyValue label="BTC amount">{formatNative(peg.amount_btc, 'BTC') ?? 'Unknown'}</KeyValue>
      <KeyValue label="Status">{peg.status ?? 'observed'}</KeyValue>
    </Section>
  );
}

function LightningChannelCloseSection({ node }: { node: InvestigationNode }) {
  const channel = (node.lightning_channel_close_data ?? node.node_data) as
    | LightningChannelCloseData
    | undefined;
  if (!channel) return null;

  return (
    <Section title="Lightning channel close">
      <KeyValue label="Channel ID"><code style={codeStyle}>{channel.channel_id}</code></KeyValue>
      <KeyValue label="Close TX">
        <code style={codeStyle}>{channel.close_tx_hash}</code>
      </KeyValue>
      <KeyValue label="Close type">{channel.close_type ?? 'unknown'}</KeyValue>
      <KeyValue label="Settled amount">{formatNative(channel.settled_btc, 'BTC') ?? 'Unknown'}</KeyValue>
      <KeyValue label="Local peer">{channel.local_alias ?? channel.local_pubkey ?? 'Unknown'}</KeyValue>
      <KeyValue label="Remote peer">{channel.remote_alias ?? channel.remote_pubkey ?? 'Unknown'}</KeyValue>
      <KeyValue label="Status">{channel.status ?? 'closed'}</KeyValue>
    </Section>
  );
}

function AtomicSwapSection({ node }: { node: InvestigationNode }) {
  const swap = (node.atomic_swap_data ?? node.node_data) as AtomicSwapData | undefined;
  if (!swap) return null;

  return (
    <Section title="Atomic swap">
      <KeyValue label="Protocol">{swap.protocol_id ?? 'Unknown'}</KeyValue>
      <KeyValue label="Route">{`${swap.source_chain} -> ${swap.destination_chain}`}</KeyValue>
      <KeyValue label="Assets">{`${swap.source_asset} -> ${swap.destination_asset}`}</KeyValue>
      <KeyValue label="Source TX">
        {swap.source_tx_hash ? <code style={codeStyle}>{swap.source_tx_hash}</code> : 'Unknown'}
      </KeyValue>
      <KeyValue label="Dest TX">
        {swap.destination_tx_hash ? <code style={codeStyle}>{swap.destination_tx_hash}</code> : 'Unknown'}
      </KeyValue>
      <KeyValue label="Hashlock">
        {swap.hashlock ? <code style={codeStyle}>{swap.hashlock}</code> : 'Unknown'}
      </KeyValue>
      <KeyValue label="Timelock">{swap.timelock ?? 'Unknown'}</KeyValue>
      <KeyValue label="State">{swap.state ?? 'partial'}</KeyValue>
    </Section>
  );
}

function UtxoSection({ node }: { node: InvestigationNode }) {
  const utxo = node.node_data as UTXONodeData;

  return (
    <Section title="UTXO details">
      <KeyValue label="Address"><code style={codeStyle}>{utxo.address}</code></KeyValue>
      <KeyValue label="Script">{utxo.address_type ?? utxo.script_type ?? 'Unknown'}</KeyValue>
      <KeyValue label="Probable change">{utxo.is_probable_change ? 'Yes' : 'No'}</KeyValue>
      <KeyValue label="CoinJoin halt">{utxo.is_coinjoin_halt ? 'Yes' : 'No'}</KeyValue>
    </Section>
  );
}

function EdgeInspectorContent({
  edge,
  canTraceForward,
  canTraceBackward,
  onTraceForward,
  onTraceBackward,
}: {
  edge: InvestigationEdge;
  canTraceForward: boolean;
  canTraceBackward: boolean;
  onTraceForward?: () => void;
  onTraceBackward?: () => void;
}) {
  const activity = edge.activity_summary;
  return (
    <div style={{ display: 'grid', gap: 18, marginTop: 18 }}>
      <div style={heroCardStyle}>
        <div style={titleBadgeStyle}>{edge.edge_type.replace(/_/g, ' ')}</div>
        <div style={heroTitleStyle}>{activity?.title ?? 'Transaction edge'}</div>
        <div style={heroSubtitleStyle}>
          {edge.direction} flow across branch {shortHash(edge.branch_id, 10, 6)}
        </div>
      </div>

      {activity && <ActivitySection summary={activity} />}

      <Section title="Value and asset">
        <KeyValue label="Asset">{edge.asset_symbol ?? 'Unknown'}</KeyValue>
        <KeyValue label="Native value">{formatNative(edge.value_native, edge.asset_symbol) ?? 'Not available'}</KeyValue>
        <KeyValue label="Fiat value">{formatUsd(edge.fiat_value_usd) ?? 'Not available'}</KeyValue>
        <KeyValue label="Timestamp">{formatTimestamp(edge.timestamp, true) ?? 'Unknown'}</KeyValue>
      </Section>

      <Section title="Trace metadata">
        <KeyValue label="Source">{shortHash(edge.source_node_id, 10, 6)}</KeyValue>
        <KeyValue label="Target">{shortHash(edge.target_node_id, 10, 6)}</KeyValue>
        <KeyValue label="TX hash">
          {edge.tx_hash ? <code style={codeStyle}>{shortHash(edge.tx_hash, 14, 8)}</code> : 'Unknown'}
        </KeyValue>
        <KeyValue label="Change output">{edge.is_suspected_change ? 'Yes' : 'No'}</KeyValue>
      </Section>

      {edge.tx_hash && (canTraceForward || canTraceBackward) && (
        <Section title="Selective trace">
          <div style={{ color: '#475569', fontSize: 12, lineHeight: 1.55 }}>
            Continue the investigation using only this transaction hash instead of
            expanding every transfer attached to the endpoint address.
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {canTraceBackward && onTraceBackward && (
              <button type="button" onClick={onTraceBackward} style={actionButtonStyle}>
                Trace input only
              </button>
            )}
            {canTraceForward && onTraceForward && (
              <button type="button" onClick={onTraceForward} style={actionButtonStyle}>
                Trace output only
              </button>
            )}
          </div>
        </Section>
      )}
    </div>
  );
}

function ActivitySection({ summary }: { summary: ActivitySummary }) {
  return (
    <Section title="Activity summary">
      <KeyValue label="Type">{summary.activity_type.replace(/_/g, ' ')}</KeyValue>
      <KeyValue label="Protocol">{summary.protocol_id ?? summary.protocol_type ?? 'Unknown'}</KeyValue>
      <KeyValue label="TX hash">
        {summary.tx_hash ? <code style={codeStyle}>{summary.tx_hash}</code> : 'Unknown'}
      </KeyValue>
      <KeyValue label="Timestamp">{formatTimestamp(summary.timestamp, true) ?? 'Unknown'}</KeyValue>
      <KeyValue label="Value">{formatNative(summary.value_native, summary.asset_symbol) ?? 'Unknown'}</KeyValue>
      <KeyValue label="Fiat">{formatUsd(summary.value_fiat) ?? 'Unknown'}</KeyValue>
      {(summary.source_chain || summary.destination_chain) && (
        <KeyValue label="Route">
          {[summary.source_chain ?? '?', summary.destination_chain ?? '?'].join(' -> ')}
        </KeyValue>
      )}
      {summary.route_summary && <KeyValue label="Summary">{summary.route_summary}</KeyValue>}
    </Section>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={sectionStyle}>
      <div style={sectionTitleStyle}>{title}</div>
      <div style={{ display: 'grid', gap: 10 }}>{children}</div>
    </section>
  );
}

function KeyValue({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={rowStyle}>
      <div style={rowLabelStyle}>{label}</div>
      <div style={rowValueStyle}>{children}</div>
    </div>
  );
}

function nodeTitle(node: InvestigationNode): string {
  switch (node.node_type) {
    case 'address': {
      const address = (node.address_data ?? node.node_data) as AddressNodeData;
      return address.entity_name ?? address.address;
    }
    case 'entity':
    case 'service':
      if (node.activity_summary?.title) return node.activity_summary.title;
      return (node.node_data as EntityNodeData & { display_name?: string }).name
        ?? (node.node_data as { display_name?: string }).display_name
        ?? 'Entity';
    case 'bridge_hop':
      return 'Cross-chain hop';
    case 'swap_event':
      return 'Swap event';
    case 'lightning_channel_open':
      return 'Lightning channel open';
    case 'lightning_channel_close':
      return 'Lightning channel close';
    case 'btc_sidechain_peg_in':
      return 'Bitcoin peg in';
    case 'btc_sidechain_peg_out':
      return 'Bitcoin peg out';
    case 'atomic_swap':
      return 'Atomic swap';
    case 'utxo':
      return (node.node_data as UTXONodeData).address;
    default:
      return node.node_type.replace(/_/g, ' ');
  }
}

function nodeSubtitle(node: InvestigationNode): string | null {
  switch (node.node_type) {
    case 'address': {
      const address = (node.address_data ?? node.node_data) as AddressNodeData;
      return `${address.chain ?? node.chain ?? 'unknown'} address`;
    }
    case 'entity':
    case 'service': {
      if (node.activity_summary) {
        return [node.activity_summary.protocol_id, node.activity_summary.tx_hash?.slice(0, 10)].filter(Boolean).join(' · ') || null;
      }
      const entity = node.node_data as EntityNodeData & { category?: string; jurisdiction?: string };
      return [entity.category, entity.jurisdiction].filter(Boolean).join(' · ') || null;
    }
    case 'bridge_hop': {
      const hop = node.node_data as BridgeHopData;
      return `${bridgeProtocolLabel(hop.protocol_id)} · ${bridgeRouteLabel(hop)}`;
    }
    case 'swap_event': {
      const swap = node.node_data as SwapEventData;
      return `${swap.input_asset} -> ${swap.output_asset}`;
    }
    case 'lightning_channel_open': {
      const channel = (node.lightning_channel_open_data ?? node.node_data) as
        | LightningChannelOpenData
        | undefined;
      return channel?.short_channel_id ?? channel?.channel_id ?? 'lightning channel';
    }
    case 'lightning_channel_close': {
      const channel = (node.lightning_channel_close_data ?? node.node_data) as
        | LightningChannelCloseData
        | undefined;
      return channel?.channel_id ?? channel?.close_tx_hash ?? 'lightning channel';
    }
    case 'btc_sidechain_peg_in':
    case 'btc_sidechain_peg_out': {
      const peg = (node.btc_sidechain_peg_data ?? node.node_data) as
        | BtcSidechainPegData
        | undefined;
      return peg ? `${peg.asset_in} -> ${peg.asset_out}` : null;
    }
    case 'atomic_swap': {
      const swap = (node.atomic_swap_data ?? node.node_data) as AtomicSwapData | undefined;
      return swap ? `${swap.source_chain} -> ${swap.destination_chain}` : null;
    }
    default:
      return null;
  }
}

const panelStyle: React.CSSProperties = {
  position: 'absolute',
  top: 0,
  right: 0,
  width: 'min(440px, 96vw)',
  maxWidth: 'calc(100vw - 8px)',
  height: '100%',
  zIndex: 120,
  background: 'rgba(255,255,255,0.96)',
  borderLeft: '1px solid rgba(148, 163, 184, 0.35)',
  padding: 18,
  overflowY: 'auto',
  overflowX: 'hidden',
  boxShadow: '-16px 0 38px rgba(15, 23, 42, 0.08)',
  backdropFilter: 'blur(14px)',
  fontFamily: '"IBM Plex Sans", "Segoe UI", sans-serif',
  boxSizing: 'border-box',
};

const collapsedPanelStyle: React.CSSProperties = {
  position: 'absolute',
  top: 0,
  right: 0,
  width: 68,
  height: '100%',
  zIndex: 120,
  background: 'rgba(255,255,255,0.94)',
  borderLeft: '1px solid rgba(148, 163, 184, 0.35)',
  boxShadow: '-12px 0 28px rgba(15, 23, 42, 0.06)',
  backdropFilter: 'blur(14px)',
  fontFamily: '"IBM Plex Sans", "Segoe UI", sans-serif',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: 14,
  paddingTop: 18,
};

const headerStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'flex-start',
  justifyContent: 'space-between',
  gap: 12,
};

const eyebrowStyle: React.CSSProperties = {
  color: '#64748b',
  fontSize: 11,
  textTransform: 'uppercase',
  letterSpacing: '0.08em',
  fontWeight: 700,
};

const titleStyle: React.CSSProperties = {
  color: '#0f172a',
  fontSize: 22,
  fontWeight: 700,
  marginTop: 4,
};

const closeButtonStyle: React.CSSProperties = {
  background: 'transparent',
  border: 'none',
  color: '#64748b',
  fontSize: 18,
  lineHeight: 1,
  cursor: 'pointer',
};

const collapseButtonStyle: React.CSSProperties = {
  width: 30,
  height: 30,
  borderRadius: 10,
  border: '1px solid rgba(148, 163, 184, 0.34)',
  background: 'rgba(255,255,255,0.92)',
  color: '#475569',
  fontSize: 15,
  lineHeight: 1,
  cursor: 'pointer',
  fontWeight: 700,
};

const collapsedLabelStyle: React.CSSProperties = {
  writingMode: 'vertical-rl',
  transform: 'rotate(180deg)',
  fontSize: 12,
  fontWeight: 800,
  letterSpacing: '0.14em',
  textTransform: 'uppercase',
  color: '#475569',
};

const collapsedMetaStyle: React.CSSProperties = {
  writingMode: 'vertical-rl',
  transform: 'rotate(180deg)',
  fontSize: 11,
  color: '#64748b',
  textTransform: 'uppercase',
  letterSpacing: '0.08em',
};

const heroCardStyle: React.CSSProperties = {
  padding: 18,
  borderRadius: 18,
  background: 'linear-gradient(180deg, rgba(248,250,252,0.98), rgba(241,245,249,0.94))',
  border: '1px solid rgba(148, 163, 184, 0.28)',
  overflow: 'hidden',
};

const titleBadgeStyle: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  padding: '3px 8px',
  borderRadius: 999,
  background: '#e0f2fe',
  color: '#0369a1',
  fontSize: 10,
  fontWeight: 800,
  textTransform: 'uppercase',
  letterSpacing: '0.06em',
};

const heroTitleStyle: React.CSSProperties = {
  color: '#0f172a',
  fontSize: 24,
  lineHeight: 1.15,
  fontWeight: 700,
  marginTop: 10,
  overflowWrap: 'anywhere',
  wordBreak: 'break-word',
};

const heroSubtitleStyle: React.CSSProperties = {
  color: '#475569',
  fontSize: 13,
  lineHeight: 1.5,
  marginTop: 6,
  overflowWrap: 'anywhere',
  wordBreak: 'break-word',
};

const sectionStyle: React.CSSProperties = {
  padding: 16,
  borderRadius: 18,
  background: '#ffffff',
  border: '1px solid rgba(148, 163, 184, 0.22)',
  overflow: 'hidden',
};

const sectionTitleStyle: React.CSSProperties = {
  color: '#0f172a',
  fontWeight: 700,
  fontSize: 14,
  marginBottom: 12,
};

const rowStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: '110px minmax(0, 1fr)',
  gap: 12,
  alignItems: 'start',
};

const rowLabelStyle: React.CSSProperties = {
  color: '#64748b',
  fontSize: 12,
  fontWeight: 700,
};

const rowValueStyle: React.CSSProperties = {
  color: '#0f172a',
  fontSize: 13,
  lineHeight: 1.5,
  minWidth: 0,
  overflowWrap: 'anywhere',
  wordBreak: 'break-word',
};

const codeStyle: React.CSSProperties = {
  display: 'block',
  fontFamily: '"IBM Plex Mono", "SFMono-Regular", monospace',
  fontSize: 12,
  color: '#0f172a',
  background: 'transparent',
  padding: 0,
  borderRadius: 0,
  whiteSpace: 'normal',
  overflowWrap: 'anywhere',
  wordBreak: 'break-word',
};

const emptyCardStyle: React.CSSProperties = {
  padding: 18,
  borderRadius: 18,
  background: '#ffffff',
  border: '1px solid rgba(148, 163, 184, 0.22)',
};

const emptyTitleStyle: React.CSSProperties = {
  color: '#0f172a',
  fontSize: 15,
  fontWeight: 700,
};

const emptyBodyStyle: React.CSSProperties = {
  color: '#475569',
  fontSize: 13,
  lineHeight: 1.6,
  marginTop: 8,
};

const actionButtonStyle: React.CSSProperties = {
  padding: '8px 12px',
  borderRadius: 999,
  border: '1px solid rgba(148, 163, 184, 0.3)',
  background: 'rgba(255,255,255,0.92)',
  color: '#334155',
  fontSize: 12,
  fontWeight: 700,
  lineHeight: 1.2,
  cursor: 'pointer',
};

const filterLabelStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 4,
  fontSize: 11,
  color: '#64748b',
  fontWeight: 600,
};

const filterInputStyle: React.CSSProperties = {
  padding: '5px 8px',
  borderRadius: 6,
  border: '1px solid rgba(148,163,184,0.4)',
  background: 'rgba(255,255,255,0.95)',
  fontSize: 12,
  color: '#1e293b',
};

const previewResultStyle: React.CSSProperties = {
  marginTop: 4,
  padding: '10px 12px',
  borderRadius: 8,
  border: '1px solid rgba(15,118,110,0.25)',
  background: 'rgba(240,253,250,0.9)',
};

const applyButtonStyle: React.CSSProperties = {
  ...actionButtonStyle,
  color: '#0f766e',
  borderColor: 'rgba(15,118,110,0.35)',
  background: 'rgba(240,253,250,0.95)',
};

function pathStoryCardStyle(tone: string, active: boolean): React.CSSProperties {
  return {
    borderRadius: 16,
    border: `1px solid ${tone}26`,
    background: active ? `${tone}10` : 'rgba(248,250,252,0.94)',
    padding: 12,
  };
}

function pathStoryEyebrowStyle(tone: string): React.CSSProperties {
  return {
    color: tone,
    fontSize: 10,
    fontWeight: 800,
    letterSpacing: '0.08em',
    textTransform: 'uppercase',
  };
}

const pathStoryTitleStyle: React.CSSProperties = {
  color: '#0f172a',
  fontSize: 13,
  fontWeight: 800,
  marginTop: 6,
};

const pathStorySubtitleStyle: React.CSSProperties = {
  color: '#475569',
  fontSize: 12,
  lineHeight: 1.5,
  marginTop: 4,
};

const pathStoryMetaStyle: React.CSSProperties = {
  color: '#64748b',
  fontSize: 11,
  lineHeight: 1.5,
  marginTop: 8,
};
