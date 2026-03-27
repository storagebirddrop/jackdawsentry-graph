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
import { MarkerType, type Node, type Edge } from '@xyflow/react';
import type {
  InvestigationNode,
  InvestigationEdge,
  ExpansionResponseV2,
} from '../types/graph';
import type { SnapshotWorkspacePreferences } from '../workspacePersistence';
import {
  normalizeInvestigationEdge as normalizeEdge,
  normalizeInvestigationNode as normalizeNode,
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
  const normalized = normalizeNode(inv);
  const colorIdx = branchColorIndex(inv.branch_id);
  return {
    id: normalized.node_id,
    type: normalized.node_type,
    position: { x: 0, y: 0 }, // ELK layout will set real positions
    data: {
      ...normalized,
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
    type: 'investigation',
    data: {
      ...inv,
      branch_color_index: colorIdx,
      branch_color: BRANCH_COLORS[colorIdx],
    },
    style: { stroke: BRANCH_COLORS[colorIdx], strokeWidth: 2 },
    animated: inv.edge_type === 'bridge_hop',
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: BRANCH_COLORS[colorIdx],
    },
  };
}

function edgeHasKnownEndpoints(edge: InvestigationEdge, nodeIds: Set<string>): boolean {
  return nodeIds.has(edge.source_node_id) && nodeIds.has(edge.target_node_id);
}

function sanitizeEdges(
  edges: Iterable<InvestigationEdge>,
  nodeIds: Set<string>,
): InvestigationEdge[] {
  const sanitized: InvestigationEdge[] = [];
  for (const edge of edges) {
    if (edgeHasKnownEndpoints(edge, nodeIds)) {
      sanitized.push(edge);
    }
  }
  return sanitized;
}

// ---------------------------------------------------------------------------
// Branch metadata
// ---------------------------------------------------------------------------

/** Metadata tracked for each active branch in the investigation graph. */
export interface BranchMeta {
  /** Stable identifier assigned by the backend (same across sessions). */
  branchId: string;
  /** CSS color string derived from branchId hash — used for the legend. */
  color: string;
  /** node_id of the node that was expanded to create this branch. */
  seedNodeId: string;
  /** Minimum depth of any node in this branch. */
  minDepth: number;
  /** Maximum depth of any node in this branch. */
  maxDepth: number;
  /** Count of nodes currently in this branch. */
  nodeCount: number;
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

  /**
   * Branch metadata map — keyed by branch_id.
   *
   * Required for collapse operations (identify which branch to collapse),
   * the branch legend in the UI (color + label), and session snapshots
   * (which branches are active).  Populated on every `applyExpansionDelta`
   * call.
   */
  branchMap: Map<string, BranchMeta>;

  /** Max node count before auto-collapse prompt */
  maxNodes: number;

  // Actions
  initSession: (sessionId: string, rootNode: InvestigationNode) => void;
  applyExpansionDelta: (response: ExpansionResponseV2) => void;
  setRfPositions: (positions: Map<string, { x: number; y: number }>) => void;
  syncRfPositions: (nodes: Array<Pick<Node, 'id' | 'position'>>) => void;
  setNodeHidden: (nodeId: string, hidden: boolean) => void;
  restoreAllHiddenNodes: () => void;
  setExpandingNode: (nodeId: string, expanding: boolean) => void;
  reset: () => void;

  /** Serialise current graph state to a JSON string (for session snapshot). */
  exportSnapshot: (options?: { workspacePreferences?: SnapshotWorkspacePreferences | null }) => string;
  /** Restore graph state from a JSON string previously produced by exportSnapshot. Returns true on success. */
  importSnapshot: (json: string) => boolean;
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
  branchMap: new Map(),
  maxNodes: 500,

  initSession(sessionId, rootNode) {
    const normalizedRoot = normalizeNode(rootNode);
    const nodeMap = new Map<string, InvestigationNode>();
    nodeMap.set(normalizedRoot.node_id, normalizedRoot);
    const seedBranch: BranchMeta = {
      branchId: normalizedRoot.branch_id,
      color: branchColor(normalizedRoot.branch_id),
      seedNodeId: normalizedRoot.node_id,
      minDepth: normalizedRoot.depth,
      maxDepth: normalizedRoot.depth,
      nodeCount: 1,
    };
    set({
      sessionId,
      nodeMap,
      edgeMap: new Map(),
      rfNodes: [toRfNode(normalizedRoot)],
      rfEdges: [],
      expandingNodeIds: new Set(),
      branchMap: new Map([[normalizedRoot.branch_id, seedBranch]]),
    });
  },

  applyExpansionDelta(response) {
    const { nodeMap, edgeMap, branchMap } = get();
    const deltaNodes = (response.nodes ?? response.added_nodes ?? []).map(normalizeNode);
    const deltaEdges = (response.edges ?? response.added_edges ?? []).map(normalizeEdge);

    // Create new Maps to ensure immutability
    const newNodeMap = new Map(nodeMap);
    let newEdgeMap = new Map(edgeMap);
    let changed = false;

    // Merge new nodes (existing nodes are NOT replaced — preserves position)
    for (const node of deltaNodes) {
      if (!newNodeMap.has(node.node_id)) {
        newNodeMap.set(node.node_id, node);
        changed = true;
      }
    }
    const validNodeIds = new Set(newNodeMap.keys());
    for (const edge of deltaEdges) {
      if (!edgeHasKnownEndpoints(edge, validNodeIds)) {
        changed = true;
        continue;
      }
      if (!newEdgeMap.has(edge.edge_id)) {
        newEdgeMap.set(edge.edge_id, edge);
        changed = true;
      }
    }

    const sanitizedEdgeMap = new Map<string, InvestigationEdge>();
    for (const [edgeId, edge] of newEdgeMap) {
      if (edgeHasKnownEndpoints(edge, validNodeIds)) {
        sanitizedEdgeMap.set(edgeId, edge);
      } else {
        changed = true;
      }
    }
    newEdgeMap = sanitizedEdgeMap;

    if (!changed) return;

    // Update branchMap: accumulate per-branch node statistics so that
    // collapse operations and the branch legend have accurate metadata.
    const newBranchMap = new Map(branchMap);
    // Count nodes per branch across the full (merged) node map.
    const branchNodeCounts = new Map<string, number>();
    const branchDepths = new Map<string, { min: number; max: number }>();
    for (const node of newNodeMap.values()) {
      branchNodeCounts.set(node.branch_id, (branchNodeCounts.get(node.branch_id) ?? 0) + 1);
      const existing = branchDepths.get(node.branch_id);
      branchDepths.set(node.branch_id, {
        min: existing ? Math.min(existing.min, node.depth) : node.depth,
        max: existing ? Math.max(existing.max, node.depth) : node.depth,
      });
    }
    // Register any branch_id seen for the first time in this delta.
    for (const node of deltaNodes) {
      if (!newBranchMap.has(node.branch_id)) {
        newBranchMap.set(node.branch_id, {
          branchId: node.branch_id,
          color: branchColor(node.branch_id),
          // The seed of a new branch is the node that was expanded — it will
          // have the lowest depth in this response (or is_seed may be set).
          seedNodeId: deltaNodes.find((n) => n.branch_id === node.branch_id && n.is_seed)?.node_id ?? node.node_id,
          minDepth: node.depth,
          maxDepth: node.depth,
          nodeCount: 0, // updated below
        });
      }
    }
    // Refresh counts and depth ranges for all active branches.
    for (const [bid, meta] of newBranchMap) {
      const depths = branchDepths.get(bid);
      newBranchMap.set(bid, {
        ...meta,
        nodeCount: branchNodeCounts.get(bid) ?? 0,
        minDepth: depths?.min ?? meta.minDepth,
        maxDepth: depths?.max ?? meta.maxDepth,
      });
    }

    // Preserve positions of existing nodes so they don't jump on delta updates.
    const existingPositions = new Map<string, { x: number; y: number }>();
    for (const n of get().rfNodes) existingPositions.set(n.id, n.position);

    set({
      nodeMap: newNodeMap,
      edgeMap: newEdgeMap,
      branchMap: newBranchMap,
      rfNodes: Array.from(newNodeMap.values()).map((inv) => {
        const rf = toRfNode(inv);
        const pos = existingPositions.get(rf.id);
        return pos ? { ...rf, position: pos } : rf;
      }),
      rfEdges: Array.from(newEdgeMap.values()).map(toRfEdge),
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

  syncRfPositions(nodes) {
    if (nodes.length === 0) return;
    const positions = new Map(
      nodes.map((node) => [node.id, node.position]),
    );
    get().setRfPositions(positions);
  },

  setNodeHidden(nodeId, hidden) {
    set((state) => {
      const existing = state.nodeMap.get(nodeId);
      if (!existing || existing.is_hidden === hidden) {
        return state;
      }

      const nextNodeMap = new Map(state.nodeMap);
      const nextNode = { ...existing, is_hidden: hidden };
      nextNodeMap.set(nodeId, nextNode);

      return {
        nodeMap: nextNodeMap,
        rfNodes: state.rfNodes.map((node) =>
          node.id === nodeId
            ? {
                ...node,
                data: {
                  ...((node.data as unknown) as InvestigationNode),
                  is_hidden: hidden,
                },
              }
            : node,
        ),
      };
    });
  },

  restoreAllHiddenNodes() {
    set((state) => {
      let changed = false;
      const nextNodeMap = new Map(state.nodeMap);
      for (const [nodeId, node] of nextNodeMap) {
        if (!node.is_hidden) continue;
        nextNodeMap.set(nodeId, { ...node, is_hidden: false });
        changed = true;
      }

      if (!changed) {
        return state;
      }

      return {
        nodeMap: nextNodeMap,
        rfNodes: state.rfNodes.map((node) => ({
          ...node,
          data: {
            ...((node.data as unknown) as InvestigationNode),
            is_hidden: false,
          },
        })),
      };
    });
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
      branchMap: new Map(),
    });
  },

  exportSnapshot(options) {
    const { sessionId, nodeMap, edgeMap, rfNodes, branchMap } = get();
    // Capture current positions from rfNodes so they survive round-trip.
    const positions: Record<string, { x: number; y: number }> = {};
    for (const n of rfNodes) positions[n.id] = n.position;
    return JSON.stringify({
      sessionId,
      nodes: Array.from(nodeMap.values()),
      edges: Array.from(edgeMap.values()),
      positions,
      branches: Array.from(branchMap.values()),
      workspacePreferences: options?.workspacePreferences ?? null,
    });
  },

  importSnapshot(json) {
    try {
      const data = JSON.parse(json) as {
        sessionId: string;
        nodes: InvestigationNode[];
        edges: InvestigationEdge[];
        positions: Record<string, { x: number; y: number }>;
        branches?: BranchMeta[];
        workspacePreferences?: SnapshotWorkspacePreferences | null;
      };
      const normalizedNodes = data.nodes.map(normalizeNode);
      const normalizedEdges = data.edges.map(normalizeEdge);
      const nodeMap = new Map<string, InvestigationNode>(
        normalizedNodes.map((n) => [n.node_id, n]),
      );
      const validNodeIds = new Set(nodeMap.keys());
      const sanitizedSnapshotEdges = sanitizeEdges(normalizedEdges, validNodeIds);
      const edgeMap = new Map<string, InvestigationEdge>(
        sanitizedSnapshotEdges.map((e) => [e.edge_id, e]),
      );
      const rfNodes = normalizedNodes.map((n) => {
        const rf = toRfNode(n);
        const pos = data.positions[n.node_id];
        return pos ? { ...rf, position: pos } : rf;
      });
      const rfEdges = sanitizedSnapshotEdges.map(toRfEdge);
      // Restore branchMap from snapshot if present; otherwise re-derive it.
      const branchMap = data.branches 
        ? new Map<string, BranchMeta>(data.branches.map((b) => [b.branchId, b]))
        : (() => {
            // Derive branchMap from nodeMap when legacy snapshots lack branches
            const derivedBranchMap = new Map<string, BranchMeta>();
            const branchNodeGroups = new Map<string, InvestigationNode[]>();
            
            // Group nodes by branch_id
            for (const node of nodeMap.values()) {
              if (!branchNodeGroups.has(node.branch_id)) {
                branchNodeGroups.set(node.branch_id, []);
              }
              branchNodeGroups.get(node.branch_id)!.push(node);
            }
            
            // Create BranchMeta entries for each branch
            for (const [branchId, nodes] of branchNodeGroups) {
              const depths = nodes.map(n => n.depth);
              derivedBranchMap.set(branchId, {
                branchId,
                color: branchColor(branchId),
                seedNodeId: nodes[0]?.node_id || '', // Use first node as seed
                minDepth: Math.min(...depths),
                maxDepth: Math.max(...depths),
                nodeCount: nodes.length,
              });
            }
            
            return derivedBranchMap;
          })();
      set({ sessionId: data.sessionId, nodeMap, edgeMap, rfNodes, rfEdges, branchMap });
      return true;
    } catch (err) {
      console.error('importSnapshot failed:', err);
      return false;
    }
  },
}));
