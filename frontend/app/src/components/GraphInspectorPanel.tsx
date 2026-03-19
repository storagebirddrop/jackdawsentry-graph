import { useMemo } from 'react';
import type { Edge, Node } from '@xyflow/react';

import type {
  ActivitySummary,
  AddressNodeData,
  BridgeHopData,
  EntityNodeData,
  InvestigationEdge,
  InvestigationNode,
  SwapEventData,
  UTXONodeData,
} from '../types/graph';
import {
  formatNative,
  formatTimestamp,
  formatUsd,
  getChainColor,
  GraphGlyph,
  glyphSurfaceStyle,
  nodeGlyphKind,
  riskColor,
  riskLabel,
  semanticBadges,
  shortHash,
} from './graphVisuals';

interface Props {
  node: Node | null;
  edge: Edge | null;
  onClose: () => void;
}

export default function GraphInspectorPanel({ node, edge, onClose }: Props) {
  const selectedNode = useMemo(
    () => (node?.data as InvestigationNode | undefined) ?? null,
    [node],
  );
  const selectedEdge = useMemo(
    () => (edge?.data as InvestigationEdge | undefined) ?? null,
    [edge],
  );

  return (
    <aside style={panelStyle}>
      <div style={headerStyle}>
        <div>
          <div style={eyebrowStyle}>Selection</div>
          <div style={titleStyle}>Inspector</div>
        </div>
        <button onClick={onClose} style={closeButtonStyle} aria-label="Close inspector">
          x
        </button>
      </div>

      {!selectedNode && !selectedEdge ? (
        <EmptyState />
      ) : selectedNode ? (
        <NodeInspectorContent node={selectedNode} />
      ) : selectedEdge ? (
        <EdgeInspectorContent edge={selectedEdge} />
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
}: {
  node: InvestigationNode;
}) {
  const accent = getChainColor(node.chain ?? (node.address_data as AddressNodeData | undefined)?.chain);
  const badges = semanticBadges(node);
  const title = nodeTitle(node);
  const subtitle = nodeSubtitle(node);
  const activity = node.activity_summary;

  return (
    <div style={{ display: 'grid', gap: 18, marginTop: 18 }}>
      <div style={heroCardStyle}>
        <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
          <div style={glyphSurfaceStyle(accent)}>
            <GraphGlyph kind={nodeGlyphKind(node)} accent={accent} />
          </div>
          <div style={{ flex: 1 }}>
            <div style={titleBadgeStyle}>{node.node_type.replace('_', ' ')}</div>
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
      {node.node_type === 'bridge_hop' && <BridgeSection node={node} />}
      {node.node_type === 'utxo' && <UtxoSection node={node} />}

      <Section title="Graph lineage">
        <KeyValue label="Branch">{shortHash(node.branch_id, 10, 6)}</KeyValue>
        <KeyValue label="Path">{shortHash(node.path_id, 10, 6)}</KeyValue>
        <KeyValue label="Lineage">{shortHash(node.lineage_id, 10, 6)}</KeyValue>
        <KeyValue label="Depth">{node.depth}</KeyValue>
        <KeyValue label="Expandability">
          {node.expandable_directions.length > 0
            ? node.expandable_directions.join(', ')
            : 'Not expandable'}
        </KeyValue>
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
  const swap = node.node_data as SwapEventData;

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

function BridgeSection({ node }: { node: InvestigationNode }) {
  const hop = node.node_data as BridgeHopData & {
    dest_chain?: string;
    dest_asset?: string;
    correlation_conf?: number;
  };
  const activity = node.activity_summary;
  const destinationChain = hop.destination_chain ?? hop.dest_chain ?? '?';
  const destinationAsset = hop.destination_asset ?? hop.dest_asset ?? '?';
  const confidence = hop.correlation_confidence ?? hop.correlation_conf;

  return (
    <Section title="Bridge hop">
      <KeyValue label="Protocol">{hop.protocol_id}</KeyValue>
      <KeyValue label="Status">{hop.status}</KeyValue>
      <KeyValue label="Mechanism">{hop.mechanism}</KeyValue>
      <KeyValue label="Route">{`${hop.source_chain} -> ${destinationChain}`}</KeyValue>
      <KeyValue label="Assets">{`${hop.source_asset ?? '?'} -> ${destinationAsset}`}</KeyValue>
      <KeyValue label="Confidence">{confidence !== undefined ? `${Math.round(confidence * 100)}%` : 'Unknown'}</KeyValue>
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

function EdgeInspectorContent({ edge }: { edge: InvestigationEdge }) {
  const activity = edge.activity_summary;
  return (
    <div style={{ display: 'grid', gap: 18, marginTop: 18 }}>
      <div style={heroCardStyle}>
        <div style={titleBadgeStyle}>{edge.edge_type.replace('_', ' ')}</div>
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
      return address.entity_name ?? shortHash(address.address, 10, 6);
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
    case 'utxo':
      return shortHash((node.node_data as UTXONodeData).address, 10, 6);
    default:
      return node.node_type.replace('_', ' ');
  }
}

function nodeSubtitle(node: InvestigationNode): string | null {
  switch (node.node_type) {
    case 'address': {
      const address = (node.address_data ?? node.node_data) as AddressNodeData;
      return `${address.chain} address`;
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
      return `${hop.source_chain} -> ${hop.destination_chain ?? '?'}`;
    }
    case 'swap_event': {
      const swap = node.node_data as SwapEventData;
      return `${swap.input_asset} -> ${swap.output_asset}`;
    }
    default:
      return null;
  }
}

const panelStyle: React.CSSProperties = {
  position: 'absolute',
  top: 0,
  right: 0,
  width: 360,
  height: '100%',
  zIndex: 120,
  background: 'rgba(255,255,255,0.96)',
  borderLeft: '1px solid rgba(148, 163, 184, 0.35)',
  padding: 20,
  overflowY: 'auto',
  boxShadow: '-16px 0 38px rgba(15, 23, 42, 0.08)',
  backdropFilter: 'blur(14px)',
  fontFamily: '"IBM Plex Sans", "Segoe UI", sans-serif',
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

const heroCardStyle: React.CSSProperties = {
  padding: 18,
  borderRadius: 18,
  background: 'linear-gradient(180deg, rgba(248,250,252,0.98), rgba(241,245,249,0.94))',
  border: '1px solid rgba(148, 163, 184, 0.28)',
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
};

const heroSubtitleStyle: React.CSSProperties = {
  color: '#475569',
  fontSize: 13,
  lineHeight: 1.5,
  marginTop: 6,
};

const sectionStyle: React.CSSProperties = {
  padding: 16,
  borderRadius: 18,
  background: '#ffffff',
  border: '1px solid rgba(148, 163, 184, 0.22)',
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
  wordBreak: 'break-word',
};

const codeStyle: React.CSSProperties = {
  fontFamily: '"IBM Plex Mono", "SFMono-Regular", monospace',
  fontSize: 12,
  color: '#1e293b',
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
