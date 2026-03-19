/**
 * InvestigationGraph — the main React Flow canvas for the investigation view.
 *
 * Responsibilities:
 * - Renders the graph from the Zustand store.
 * - Triggers ELK layout after delta updates.
 * - Handles node expand clicks (dispatches to API, then applies delta).
 * - Shows overload warning at NODE_OVERLOAD_THRESHOLD.
 * - Hosts the filter panel (client-side node/edge hiding).
 * - Opens the bridge hop side drawer on BridgeHopNode click.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type EdgeMouseHandler,
  type NodeMouseHandler,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { useGraphStore } from '../store/graphStore';
import { expandNode } from '../api/client';
import { computeElkLayout } from '../layout/elkLayout';
import type { ExpandRequest, InvestigationNode } from '../types/graph';

import AddressNode from './nodes/AddressNode';
import EntityNode from './nodes/EntityNode';
import BridgeHopNode from './nodes/BridgeHopNode';
import ClusterSummaryNode from './nodes/ClusterSummaryNode';
import UTXONode from './nodes/UTXONode';
import SolanaInstructionNode from './nodes/SolanaInstructionNode';
import SwapEventNode from './nodes/SwapEventNode';
import LightningChannelOpenNode from './nodes/LightningChannelOpenNode';
import LightningChannelCloseNode from './nodes/LightningChannelCloseNode';
import BtcSidechainPegNode from './nodes/BtcSidechainPegNode';
import AtomicSwapNode from './nodes/AtomicSwapNode';
import FilterPanel, { type FilterState, DEFAULT_FILTERS } from './FilterPanel';
import GraphAppearancePanel from './GraphAppearancePanel';
import GraphInspectorPanel from './GraphInspectorPanel';
import InvestigationEdgeComponent from './edges/InvestigationEdge';
import {
  DEFAULT_GRAPH_APPEARANCE,
  type GraphAppearanceState,
} from './graphAppearance';
import { isNodeVisibleInView } from './graphVisuals';

const NODE_TYPES = {
  address: AddressNode,
  entity: EntityNode,
  bridge_hop: BridgeHopNode,
  cluster_summary: ClusterSummaryNode,
  utxo: UTXONode,
  swap_event: SwapEventNode,
  lightning_channel_open: LightningChannelOpenNode,
  lightning_channel_close: LightningChannelCloseNode,
  btc_sidechain_peg_in: BtcSidechainPegNode,
  btc_sidechain_peg_out: BtcSidechainPegNode,
  atomic_swap: AtomicSwapNode,
  service: EntityNode,
  solana_instruction: SolanaInstructionNode,
};

const EDGE_TYPES = {
  investigation: InvestigationEdgeComponent,
};

const NODE_OVERLOAD_THRESHOLD = 500;

interface Props {
  sessionId: string;
}

/** Apply filter state to raw nodes/edges, returning the visible subset. */
function applyFilters(
  nodes: Node[],
  edges: Edge[],
  filters: FilterState,
  appearance: GraphAppearanceState,
): { nodes: Node[]; edges: Edge[] } {
  let visibleNodes = nodes.filter((node) => {
    const data = node.data as unknown as InvestigationNode;
    return isNodeVisibleInView(data, appearance.viewMode);
  });
  let visibleEdges = edges;

  let visibleIds = new Set(visibleNodes.map((n) => n.id));
  visibleEdges = visibleEdges.filter(
    (e) => visibleIds.has(e.source) && visibleIds.has(e.target),
  );

  if (filters.chainFilter.length > 0) {
    visibleNodes = visibleNodes.filter((n) => {
      const chain = (n.data as Record<string, unknown>)?.chain as string | undefined;
      return !chain || filters.chainFilter.includes(chain);
    });
    visibleIds = new Set(visibleNodes.map((n) => n.id));
    visibleEdges = visibleEdges.filter(
      (e) => visibleIds.has(e.source) && visibleIds.has(e.target),
    );
  }

  if (filters.maxDepth < 20) {
    visibleNodes = visibleNodes.filter((n) => {
      const depth = (n.data as Record<string, unknown>)?.depth as number | undefined;
      return depth === undefined || depth <= filters.maxDepth;
    });
    visibleIds = new Set(visibleNodes.map((n) => n.id));
    visibleEdges = visibleEdges.filter(
      (e) => visibleIds.has(e.source) && visibleIds.has(e.target),
    );
  }

  const minFiat = filters.minFiatValue;
  if (minFiat !== null && minFiat > 0) {
    visibleEdges = visibleEdges.filter((e) => {
      const val = (e.data as Record<string, unknown>)?.fiat_value_usd as number | undefined;
      return val === undefined || val >= minFiat;
    });
  }

  if (filters.assetFilter.trim()) {
    const q = filters.assetFilter.trim().toLowerCase();
    visibleEdges = visibleEdges.filter((e) => {
      const sym = (e.data as Record<string, unknown>)?.asset_symbol as string | undefined;
      return !sym || sym.toLowerCase().includes(q);
    });
  }

  return { nodes: visibleNodes, edges: visibleEdges };
}

export default function InvestigationGraph({ sessionId }: Props) {
  const {
    rfNodes,
    rfEdges,
    setRfPositions,
    applyExpansionDelta,
    setExpandingNode,
    expandingNodeIds,
    exportSnapshot,
    importSnapshot,
  } = useGraphStore();

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(rfNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(rfEdges);

  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  const [filterVisible, setFilterVisible] = useState(false);
  const [appearance, setAppearance] = useState<GraphAppearanceState>(DEFAULT_GRAPH_APPEARANCE);
  const [appearanceVisible, setAppearanceVisible] = useState(false);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(false);

  // Keep local RF state in sync with store, applying current filters
  useEffect(() => {
    const { nodes: fn, edges: fe } = applyFilters(rfNodes, rfEdges, filters, appearance);
    setNodes(fn);
    setEdges(fe);
  }, [rfNodes, rfEdges, filters, appearance, setNodes, setEdges]);

  // Incremental ELK layout: only newly added nodes are placed freely.
  // Nodes that have already been laid out are passed to ELK as fixed-position
  // hints (enabling interactiveLayout mode) and their returned positions are
  // discarded — preserving the investigator's mental map across expansions.
  const layoutedNodeIds = useRef<Set<string>>(new Set());
  const layoutRef = useRef<number | null>(null);
  useEffect(() => {
    const newNodes = rfNodes.filter((n) => !layoutedNodeIds.current.has(n.id));
    if (newNodes.length === 0) return; // Nothing new to place.

    const currentLayout = Date.now();
    layoutRef.current = currentLayout;

    // Build fixed-position map for already-laid-out nodes so ELK treats them
    // as anchors when computing layer assignments for new nodes.
    const fixedPositions = new Map<string, { x: number; y: number }>();
    for (const n of rfNodes) {
      if (layoutedNodeIds.current.has(n.id)) {
        fixedPositions.set(n.id, n.position);
      }
    }

    // Snapshot the IDs being laid out in this pass before the async gap.
    const passingNewIds = new Set(newNodes.map((n) => n.id));

    computeElkLayout(rfNodes, rfEdges, fixedPositions)
      .then((positions) => {
        if (layoutRef.current !== currentLayout) return;
        // Apply ELK output only for nodes placed in this pass — existing
        // nodes retain their current positions regardless of what ELK returns.
        const deltaPositions = new Map<string, { x: number; y: number }>();
        for (const [id, pos] of positions) {
          if (passingNewIds.has(id)) deltaPositions.set(id, pos);
        }
        // Mark all nodes submitted to ELK in this pass as laid out
        // so a rapid second expansion doesn't re-layout same nodes.
        for (const nodeId of passingNewIds) layoutedNodeIds.current.add(nodeId);
        setRfPositions(deltaPositions);
      })
      .catch((error) => {
        console.error('ELK layout computation failed:', error);
      });
  }, [rfNodes, rfEdges, setRfPositions]);

  // Expand a node in a given direction
  const handleExpand = useCallback(
    async (
      node: Pick<InvestigationNode, 'node_id' | 'lineage_id'>,
      operation: ExpandRequest['operation_type'],
    ) => {
      if (expandingNodeIds.has(node.node_id)) return;
      if (rfNodes.length >= NODE_OVERLOAD_THRESHOLD) {
        alert(`Graph has ${rfNodes.length} nodes. Collapse some branches before expanding further.`);
        return;
      }
      setExpandingNode(node.node_id, true);
      try {
        const response = await expandNode(sessionId, {
          seed_node_id: node.node_id,
          seed_lineage_id: node.lineage_id,
          operation_type: operation,
        });
        applyExpansionDelta(response);
      } catch (err) {
        console.error('Expand failed:', err);
      } finally {
        setExpandingNode(node.node_id, false);
      }
    },
    [sessionId, rfNodes.length, expandingNodeIds, setExpandingNode, applyExpansionDelta],
  );

  const handleNodeClick: NodeMouseHandler = useCallback((_evt, node) => {
    setSelectedNodeId(node.id);
    setSelectedEdgeId(null);
  }, []);

  const handleEdgeClick: EdgeMouseHandler = useCallback((_evt, edge) => {
    setSelectedEdgeId(edge.id);
    setSelectedNodeId(null);
  }, []);

  // Inject expand handlers into node data
  const enrichedNodes = nodes.map((n) => {
    const invNode = n.data as unknown as InvestigationNode;
    return {
      ...n,
      data: {
        ...n.data,
        onExpandNext: () => handleExpand(invNode, 'expand_next'),
        onExpandPrev: () => handleExpand(invNode, 'expand_prev'),
        isExpanding: expandingNodeIds.has(n.id),
        appearance,
      },
    };
  });

  const enrichedEdges = edges.map((edge) => ({
    ...edge,
    data: {
      ...(edge.data as Record<string, unknown>),
      appearance,
    },
  }));

  const selectedNode = useMemo(
    () => enrichedNodes.find((node) => node.id === selectedNodeId) ?? null,
    [enrichedNodes, selectedNodeId],
  );
  const selectedEdge = useMemo(
    () => enrichedEdges.find((edge) => edge.id === selectedEdgeId) ?? null,
    [enrichedEdges, selectedEdgeId],
  );

  useEffect(() => {
    if (selectedNodeId || selectedEdgeId) {
      setInspectorCollapsed(false);
    }
  }, [selectedNodeId, selectedEdgeId]);

  return (
    <div
      style={{
        width: '100%',
        height: '100vh',
        background:
          'radial-gradient(circle at top left, rgba(191,219,254,0.28), transparent 30%), linear-gradient(180deg, #f8fafc 0%, #eef2ff 100%)',
        position: 'relative',
      }}
    >

      {/* Toolbar */}
      <div
        style={{
          position: 'absolute',
          top: 16,
          left: 16,
          zIndex: 100,
          display: 'flex',
          gap: 10,
          alignItems: 'center',
          flexWrap: 'wrap',
        }}
      >
        <button
          onClick={() => {
            setAppearanceVisible(false);
            setFilterVisible((v) => !v);
          }}
          style={toolbarBtnStyle}
        >
          Filters
          {[
            filters.chainFilter.length > 0,
            filters.minFiatValue !== null && filters.minFiatValue > 0,
            filters.maxDepth !== undefined && filters.maxDepth < 20,
            filters.assetFilter !== undefined && filters.assetFilter.length > 0
          ].filter(Boolean).length > 0 ? ' •' : ''}
        </button>
        <button
          onClick={() => {
            setFilterVisible(false);
            setAppearanceVisible((v) => !v);
          }}
          style={toolbarBtnStyle}
        >
          Appearance
        </button>
        <button
          onClick={() => {
            const json = exportSnapshot();
            const a = document.createElement('a');
            const blobUrl = URL.createObjectURL(new Blob([json], { type: 'application/json' }));
            a.href = blobUrl;
            a.download = `session-${sessionId.slice(0, 8)}.json`;
            a.click();
            // Clean up blob URL to prevent memory leaks
            setTimeout(() => URL.revokeObjectURL(blobUrl), 100);
          }}
          style={toolbarBtnStyle}
          title="Save session snapshot"
        >
          Export
        </button>
        <label style={{ ...toolbarBtnStyle, cursor: 'pointer' }} title="Restore session snapshot">
          Import
          <input
            type="file"
            accept=".json"
            style={{ display: 'none' }}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              file.text()
                .then((text) => {
                  importSnapshot(text);
                  e.target.value = '';
                })
                .catch((error) => {
                  console.error('Failed to import session snapshot:', error);
                  alert('Failed to import session snapshot. Please check the file format.');
                  e.target.value = '';
                });
            }}
          />
        </label>
        <span style={toolbarPillStyle}>
          {appearance.viewMode} view
        </span>
        <span style={toolbarPillStyle}>
          {appearance.interactionMode} mode
        </span>
        <span style={{ color: '#475569', fontSize: 12, alignSelf: 'center', fontWeight: 600 }}>
          {rfNodes.length} nodes · {rfEdges.length} edges
        </span>
      </div>

      {/* Filter panel */}
      <FilterPanel
        filters={filters}
        onChange={setFilters}
        visible={filterVisible}
        onClose={() => setFilterVisible(false)}
      />
      <GraphAppearancePanel
        appearance={appearance}
        visible={appearanceVisible}
        onClose={() => setAppearanceVisible(false)}
        onChange={setAppearance}
      />

      <ReactFlow
        nodes={enrichedNodes}
        edges={enrichedEdges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        onEdgeClick={handleEdgeClick}
        onPaneClick={() => {
          setSelectedNodeId(null);
          setSelectedEdgeId(null);
        }}
        fitView
        minZoom={0.1}
        maxZoom={2.2}
        panOnDrag={appearance.interactionMode === 'grab'}
        nodesDraggable={appearance.interactionMode === 'move'}
        selectionOnDrag={appearance.interactionMode === 'move'}
      >
        {appearance.showGrid && (
          <Background color="#cbd5e1" gap={24} size={1.25} variant={BackgroundVariant.Dots} />
        )}
        <Controls />
        {appearance.showMiniMap && (
          <MiniMap
            nodeColor={(n) => (n.data as { branch_color?: string }).branch_color ?? '#3b82f6'}
            style={{
              background: 'rgba(255,255,255,0.94)',
              border: '1px solid rgba(148, 163, 184, 0.4)',
            }}
            maskColor="rgba(226,232,240,0.65)"
          />
        )}
      </ReactFlow>

      <GraphInspectorPanel
        node={selectedNode}
        edge={selectedEdge}
        collapsed={inspectorCollapsed}
        onClose={() => {
          setSelectedNodeId(null);
          setSelectedEdgeId(null);
        }}
        onToggleCollapsed={() => setInspectorCollapsed((value) => !value)}
      />
    </div>
  );
}

const toolbarBtnStyle: React.CSSProperties = {
  padding: '8px 14px',
  background: 'rgba(255,255,255,0.9)',
  border: '1px solid rgba(148, 163, 184, 0.4)',
  borderRadius: 999,
  color: '#0f172a',
  fontSize: 12,
  fontWeight: 700,
  cursor: 'pointer',
  fontFamily: '"IBM Plex Sans", "Segoe UI", sans-serif',
  boxShadow: '0 8px 24px rgba(15, 23, 42, 0.08)',
};

const toolbarPillStyle: React.CSSProperties = {
  padding: '7px 12px',
  borderRadius: 999,
  background: 'rgba(219, 234, 254, 0.9)',
  border: '1px solid rgba(96, 165, 250, 0.36)',
  color: '#1d4ed8',
  fontSize: 12,
  fontWeight: 700,
  textTransform: 'capitalize',
};
