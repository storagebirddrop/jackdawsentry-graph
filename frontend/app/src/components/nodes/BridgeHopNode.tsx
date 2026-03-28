/**
 * BridgeHopNode — displays a cross-chain bridge hop with source→dest chain,
 * protocol name, assets, confidence, and status indicator.
 */

import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { InvestigationNode, BridgeHopData } from '../../types/graph';
import {
  DEFAULT_GRAPH_APPEARANCE,
  type GraphAppearanceState,
} from '../graphAppearance';
import {
  badgeStyle,
  bridgeAssetRouteLabel,
  bridgeMechanismLabel,
  getBridgeProtocolColor,
  bridgeProtocolLabel,
  bridgeRouteLabel,
  bridgeStatusTone,
  GraphGlyph,
  glyphSurfaceStyle,
  nodeGlyphKind,
  valueChipStyle,
} from '../graphVisuals';

interface BridgeNodeData extends InvestigationNode {
  branch_color: string;
  appearance?: GraphAppearanceState;
}

export default function BridgeHopNode({ data }: NodeProps) {
  const d = data as unknown as BridgeNodeData;
  const hop = d.node_data as BridgeHopData & {
    dest_chain?: string;
    dest_asset?: string;
    correlation_conf?: number;
  };
  const appearance = d.appearance ?? DEFAULT_GRAPH_APPEARANCE;
  const activity = d.activity_summary;
  const protocolLabel = bridgeProtocolLabel(hop.protocol_id);
  const destinationChain = hop.destination_chain ?? hop.dest_chain;
  const destinationAsset = hop.destination_asset ?? hop.dest_asset;
  const confidence = hop.correlation_confidence ?? hop.correlation_conf;
  const unresolvedCorrelation =
    hop.status === 'pending'
    || destinationChain == null
    || activity?.destination_tx_hash == null;

  // Defensive guards for required properties
  if (!hop?.hop_id) {
    return (
      <div style={{ border: '2px solid #ef4444', borderRadius: 8, background: '#1e1b4b', color: '#f1f5f9', padding: '6px 10px', minWidth: 180, fontSize: 11 }}>
        <Handle type="target" position={Position.Left} />
        <div style={{ color: '#f87171' }}>Invalid Bridge Hop Data</div>
        <Handle type="source" position={Position.Right} />
      </div>
    );
  }

  const bridgeAccent = getBridgeProtocolColor(hop.protocol_id);
  const statusTone = bridgeStatusTone(hop.status || 'pending');
  const routeLabel = bridgeRouteLabel({
    source_chain: hop.source_chain,
    destination_chain: destinationChain,
  });
  const assetRouteLabel = bridgeAssetRouteLabel({
    source_asset: hop.source_asset,
    destination_asset: destinationAsset,
    destination_chain: destinationChain,
  });
  const mechanismLabel = bridgeMechanismLabel(hop.mechanism);

  return (
    <div
      style={{
        border: `1px solid ${bridgeAccent}44`,
        borderRadius: 20,
        background: `linear-gradient(180deg, ${bridgeAccent}10, rgba(255,255,255,0.98) 24%)`,
        color: '#0f172a',
        padding: '15px 16px',
        minWidth: 260,
        fontSize: 11,
        boxShadow: `0 18px 36px ${bridgeAccent}16, 0 12px 28px rgba(15, 23, 42, 0.08)`,
        fontFamily: '"IBM Plex Sans", "Segoe UI", sans-serif',
      }}
    >
      <Handle type="target" position={Position.Left} />
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        {appearance.showEntityIcons && (
          <div style={glyphSurfaceStyle(bridgeAccent)}>
            <GraphGlyph kind={nodeGlyphKind(d)} accent={bridgeAccent} />
          </div>
        )}
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
            <div>
              <div style={{ color: '#64748b', fontSize: 10, fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
                Bridge hop
              </div>
              <div style={{ color: bridgeAccent, fontWeight: 700, fontSize: 15, marginTop: 4 }}>
                {protocolLabel}
              </div>
            </div>
            <span
              style={{
                ...badgeStyle(statusTone),
                textTransform: 'uppercase',
              }}
            >
              {hop.status || 'pending'}
            </span>
          </div>

          <div style={{ color: '#0f172a', marginTop: 10, fontSize: 13, fontWeight: 700 }}>
            {routeLabel}
          </div>

          {(hop.source_asset || destinationAsset) && (
            <div style={{ color: '#475569', fontSize: 12, marginTop: 5 }}>
              {assetRouteLabel}
            </div>
          )}

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 10 }}>
            <span style={badgeStyle(bridgeAccent)}>{mechanismLabel}</span>
            {Number.isFinite(confidence) && (
              <span style={valueChipStyle(bridgeAccent)}>
                {(confidence * 100).toFixed(0)}% {unresolvedCorrelation ? 'correlation confidence' : 'confidence'}
              </span>
            )}
          </div>
          {activity?.source_tx_hash && (
            <div style={{ color: '#64748b', fontSize: 11, marginTop: 6 }}>
              tx {activity.source_tx_hash.slice(0, 10)}…
              {activity.destination_tx_hash && ` -> ${activity.destination_tx_hash.slice(0, 10)}…`}
            </div>
          )}
          {(hop.source_amount !== undefined || hop.destination_amount !== undefined) && (
            <div style={{ color: '#475569', fontSize: 11, marginTop: 8 }}>
              {hop.source_amount !== undefined
                ? `${hop.source_amount.toFixed(3)} ${hop.source_asset ?? ''}`.trim()
                : '?'}
              {' -> '}
              {hop.destination_amount !== undefined && hop.destination_amount !== null
                ? `${hop.destination_amount.toFixed(3)} ${destinationAsset ?? ''}`.trim()
                : 'pending'}
            </div>
          )}
        </div>
      </div>

      <Handle type="source" position={Position.Right} />
    </div>
  );
}
