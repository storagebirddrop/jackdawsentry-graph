/**
 * Jackdaw Sentry — Investigation graph state store (Zustand).
 *
 * Single source of truth for everything currently visible on the canvas.
 * The store is NOT rebuilt from API responses on each call; it receives
 * delta updates via `applyExpansionDelta`.
 *
 * React Flow node/edge objects are derived from the canonical
 * InvestigationNode/InvestigationEdge types.
 */

import { create } from 'zustand';
import type { Node, Edge } from '@xyflow/react';
import type {
  InvestigationNode,
  InvestigationEdge,
  ExpansionResponseV2,
} from '../types/graph';

// ---------------------------------------------------------------------------
// Branch color palette (8 colors, cycling by branch_id hash)
// ---------------------------------------------------------------------------

const BRANCH_COLORS = [
  '#3b82f6', // blue
  '#10b981', // emerald
  '#f59e0b', // amber
  '#8b5cf6', // violet
  '#ef4444', // red
  '#06b6d4', // cyan
  '#f97316', // orange
  '#84cc16', // lime
] as const;

function branchColorIndex(branchId: string): number {
  let hash = 0;
  for (let i = 0; i < branchId.length; i++) {
    hash = (hash * 31 + branchId.charCodeAt(i)) >>> 0;
  }
  return hash % BRANCH_COLORS.length;
}

export function branchColor(branchId: string): string {
  return BRANCH_COLORS[branchColorIndex(branchId)];
}

// ---------------------------------------------------------------------------
// Conversion helpers: InvestigationNode → React Flow Node
// ---------------------------------------------------------------------------

export function toRfNode(inv: InvestigationNode): Node {
  const colorIdx = branchColorIndex(inv.branch_id);
  return {
    id: inv.node_id,
    type: inv.node_type,
    position: { x: 0, y: 0 }, // ELK layout will set real positions
    data: {
      ...inv,
      branch_color_index: colorIdx,
      branch_color: BRANCH_COLORS[colorIdx],
    },
  };
}

export function toRfEdge(inv: InvestigationEdge): Edge {
  const colorIdx = branchColorIndex(inv.branch_id);
  return {
    id: inv.edge_id,
    source: inv.source_node_id,
    target: inv.target_node_id,
    type: 'default',
    data: {
      ...inv,
      branch_color_index: colorIdx,
      branch_color: BRANCH_COLORS[colorIdx],
    },
    style: { stroke: BRANCH_COLORS[colorIdx], strokeWidth: 2 },
    animated: inv.edge_type === 'bridge_hop',
  };
}

// ---------------------------------------------------------------------------
// State shape
// ---------------------------------------------------------------------------

export interface GraphState {
  sessionId: string | null;

  /** Canonical node map — keyed by node_id */
  nodeMap: Map<string, InvestigationNode>;
  /** Canonical edge map — keyed by edge_id */
  edgeMap: Map<string, InvestigationEdge>;

  /** React Flow nodes (derived, updated by `applyExpansionDelta`) */
  rfNodes: Node[];
  /** React Flow edges (derived, updated by `applyExpansionDelta`) */
  rfEdges: Edge[];

  /** Nodes that are currently being expanded (to show loading state) */
  expandingNodeIds: Set<string>;

  /** Max node count before auto-collapse prompt */
  maxNodes: number;

  // Actions
  initSession: (sessionId: string, rootNode: InvestigationNode) => void;
  applyExpansionDelta: (response: ExpansionResponseV2) => void;
  setRfPositions: (positions: Map<string, { x: number; y: number }>) => void;
  setExpandingNode: (nodeId: string, expanding: boolean) => void;
  reset: () => void;
}

// ---------------------------------------------------------------------------
// Store implementation
// ---------------------------------------------------------------------------

export const useGraphStore = create<GraphState>((set, get) => ({
  sessionId: null,
  nodeMap: new Map(),
  edgeMap: new Map(),
  rfNodes: [],
  rfEdges: [],
  expandingNodeIds: new Set(),
  maxNodes: 500,

  initSession(sessionId, rootNode) {
    const nodeMap = new Map<string, InvestigationNode>();
    nodeMap.set(rootNode.node_id, rootNode);
    set({
      sessionId,
      nodeMap,
      edgeMap: new Map(),
      rfNodes: [toRfNode(rootNode)],
      rfEdges: [],
    });
  },

  applyExpansionDelta(response) {
    const { nodeMap, edgeMap } = get();

    // Merge new nodes (existing nodes are NOT replaced — preserves position)
    let changed = false;
    for (const node of response.nodes) {
      if (!nodeMap.has(node.node_id)) {
        nodeMap.set(node.node_id, node);
        changed = true;
      }
    }
    for (const edge of response.edges) {
      if (!edgeMap.has(edge.edge_id)) {
        edgeMap.set(edge.edge_id, edge);
        changed = true;
      }
    }

    if (!changed) return;

    set({
      nodeMap: new Map(nodeMap),
      edgeMap: new Map(edgeMap),
      rfNodes: Array.from(nodeMap.values()).map(toRfNode),
      rfEdges: Array.from(edgeMap.values()).map(toRfEdge),
    });
  },

  setRfPositions(positions) {
    set((state) => ({
      rfNodes: state.rfNodes.map((n) => {
        const pos = positions.get(n.id);
        return pos ? { ...n, position: pos } : n;
      }),
    }));
  },

  setExpandingNode(nodeId, expanding) {
    set((state) => {
      const next = new Set(state.expandingNodeIds);
      if (expanding) next.add(nodeId);
      else next.delete(nodeId);
      return { expandingNodeIds: next };
    });
  },

  reset() {
    set({
      sessionId: null,
      nodeMap: new Map(),
      edgeMap: new Map(),
      rfNodes: [],
      rfEdges: [],
      expandingNodeIds: new Set(),
    });
  },
}));
