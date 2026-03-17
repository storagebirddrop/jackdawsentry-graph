/**
 * InvestigationGraph — the main React Flow canvas for the investigation view.
 *
 * Responsibilities:
 * - Renders the graph from the Zustand store.
 * - Triggers ELK layout after delta updates.
 * - Handles node expand clicks (dispatches to API, then applies delta).
 * - Shows overload warning at NODE_OVERLOAD_THRESHOLD.
 */

import { useCallback, useEffect, useRef } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { useGraphStore } from '../store/graphStore';
import { expandNode } from '../api/client';
import { computeElkLayout } from '../layout/elkLayout';
import type { ExpandRequest } from '../types/graph';

import AddressNode from './nodes/AddressNode';
import EntityNode from './nodes/EntityNode';
import BridgeHopNode from './nodes/BridgeHopNode';
import ClusterSummaryNode from './nodes/ClusterSummaryNode';

const NODE_TYPES = {
  address: AddressNode,
  entity: EntityNode,
  bridge_hop: BridgeHopNode,
  cluster_summary: ClusterSummaryNode,
  // Remaining types render as address-style by default
  utxo: AddressNode,
  swap_event: BridgeHopNode,
  service: EntityNode,
  solana_instruction: AddressNode,
};

const NODE_OVERLOAD_THRESHOLD = 500;

interface Props {
  sessionId: string;
}

export default function InvestigationGraph({ sessionId }: Props) {
  const {
    rfNodes,
    rfEdges,
    setRfPositions,
    applyExpansionDelta,
    setExpandingNode,
    expandingNodeIds,
  } = useGraphStore();

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(rfNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(rfEdges);

  // Keep local RF state in sync with store
  useEffect(() => {
    setNodes(rfNodes);
  }, [rfNodes, setNodes]);

  useEffect(() => {
    setEdges(rfEdges);
  }, [rfEdges, setEdges]);

  // Re-run ELK layout whenever node/edge count changes
  const prevNodeCount = useRef(0);
  useEffect(() => {
    if (rfNodes.length === prevNodeCount.current) return;
    prevNodeCount.current = rfNodes.length;

    computeElkLayout(rfNodes, rfEdges).then((positions) => {
      setRfPositions(positions);
    });
  }, [rfNodes.length, rfEdges.length, rfNodes, rfEdges, setRfPositions]);

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

  // Inject expand handler into node data
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
    <div style={{ width: '100%', height: '100vh', background: '#0f172a' }}>
      <ReactFlow
        nodes={enrichedNodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
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
    </div>
  );
}
