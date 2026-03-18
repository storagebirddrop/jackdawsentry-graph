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

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type NodeMouseHandler,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { useGraphStore } from '../store/graphStore';
import { expandNode } from '../api/client';
import { computeElkLayout } from '../layout/elkLayout';
import type { ExpandRequest, BridgeHopData } from '../types/graph';

import AddressNode from './nodes/AddressNode';
import EntityNode from './nodes/EntityNode';
import BridgeHopNode from './nodes/BridgeHopNode';
import ClusterSummaryNode from './nodes/ClusterSummaryNode';
import UTXONode from './nodes/UTXONode';
import SolanaInstructionNode from './nodes/SolanaInstructionNode';
import SwapEventNode from './nodes/SwapEventNode';
import FilterPanel, { type FilterState, DEFAULT_FILTERS } from './FilterPanel';
import BridgeHopDrawer from './BridgeHopDrawer';

const NODE_TYPES = {
  address: AddressNode,
  entity: EntityNode,
  bridge_hop: BridgeHopNode,
  cluster_summary: ClusterSummaryNode,
  utxo: UTXONode,
  swap_event: SwapEventNode,
  service: EntityNode,
  solana_instruction: SolanaInstructionNode,
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
): { nodes: Node[]; edges: Edge[] } {
  let visibleNodes = nodes;
  let visibleEdges = edges;

  if (filters.chainFilter.length > 0) {
    visibleNodes = visibleNodes.filter((n) => {
      const chain = (n.data as Record<string, unknown>)?.chain as string | undefined;
      return !chain || filters.chainFilter.includes(chain);
    });
    const visibleIds = new Set(visibleNodes.map((n) => n.id));
    visibleEdges = visibleEdges.filter(
      (e) => visibleIds.has(e.source) && visibleIds.has(e.target),
    );
  }

  if (filters.maxDepth < 20) {
    visibleNodes = visibleNodes.filter((n) => {
      const depth = (n.data as Record<string, unknown>)?.depth as number | undefined;
      return depth === undefined || depth <= filters.maxDepth;
    });
    const visibleIds = new Set(visibleNodes.map((n) => n.id));
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

  const [selectedBridgeNode, setSelectedBridgeNode] = useState<{
    nodeId: string;
    hopData: BridgeHopData;
  } | null>(null);

  // Keep local RF state in sync with store, applying current filters
  useEffect(() => {
    const { nodes: fn, edges: fe } = applyFilters(rfNodes, rfEdges, filters);
    setNodes(fn);
    setEdges(fe);
  }, [rfNodes, rfEdges, filters, setNodes, setEdges]);

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
    async (nodeId: string, operation: ExpandRequest['operation'], chain?: string) => {
      if (expandingNodeIds.has(nodeId)) return;
      if (rfNodes.length >= NODE_OVERLOAD_THRESHOLD) {
        alert(`Graph has ${rfNodes.length} nodes. Collapse some branches before expanding further.`);
        return;
      }
      setExpandingNode(nodeId, true);
      try {
        const response = await expandNode(sessionId, { node_id: nodeId, operation, chain });
        applyExpansionDelta(response);
      } catch (err) {
        console.error('Expand failed:', err);
      } finally {
        setExpandingNode(nodeId, false);
      }
    },
    [sessionId, rfNodes.length, expandingNodeIds, setExpandingNode, applyExpansionDelta],
  );

  // Open bridge drawer on BridgeHopNode click
  const handleNodeClick: NodeMouseHandler = useCallback((_evt, node) => {
    if (node.type === 'bridge_hop') {
      const nodeData = node.data as Record<string, unknown> | undefined;
      if (nodeData && nodeData.node_data != null) {
        setSelectedBridgeNode({ nodeId: node.id, hopData: nodeData.node_data as BridgeHopData });
      }
    }
  }, []);

  // Inject expand handlers into node data
  const enrichedNodes = nodes.map((n) => ({
    ...n,
    data: {
      ...n.data,
      onExpandNext: () => handleExpand(n.id, 'expand_next'),
      onExpandPrev: () => handleExpand(n.id, 'expand_prev'),
      isExpanding: expandingNodeIds.has(n.id),
    },
  }));

  return (
    <div style={{ width: '100%', height: '100vh', background: '#0f172a', position: 'relative' }}>

      {/* Toolbar */}
      <div
        style={{
          position: 'absolute',
          top: 12,
          left: 12,
          zIndex: 100,
          display: 'flex',
          gap: 8,
        }}
      >
        <button
          onClick={() => setFilterVisible((v) => !v)}
          style={toolbarBtnStyle}
        >
          Filters {filters.chainFilter.length + (filters.minFiatValue !== null && filters.minFiatValue > 0 ? 1 : 0) > 0 ? '●' : ''}
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
          Save
        </button>
        <label style={{ ...toolbarBtnStyle, cursor: 'pointer' }} title="Restore session snapshot">
          Restore
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
        <span style={{ color: '#475569', fontSize: 11, alignSelf: 'center' }}>
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

      <ReactFlow
        nodes={enrichedNodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        fitView
        minZoom={0.1}
        maxZoom={2}
      >
        <Background color="#1e293b" gap={24} />
        <Controls />
        <MiniMap
          nodeColor={(n) => (n.data as { branch_color?: string }).branch_color ?? '#3b82f6'}
          style={{ background: '#1e293b' }}
        />
      </ReactFlow>

      {/* Bridge hop side drawer */}
      {selectedBridgeNode && (
        <BridgeHopDrawer
          sessionId={sessionId}
          nodeId={selectedBridgeNode.nodeId}
          hopData={selectedBridgeNode.hopData}
          onClose={() => setSelectedBridgeNode(null)}
        />
      )}
    </div>
  );
}

const toolbarBtnStyle: React.CSSProperties = {
  padding: '4px 12px',
  background: '#1e293b',
  border: '1px solid #334155',
  borderRadius: 5,
  color: '#94a3b8',
  fontSize: 11,
  cursor: 'pointer',
  fontFamily: 'sans-serif',
};
