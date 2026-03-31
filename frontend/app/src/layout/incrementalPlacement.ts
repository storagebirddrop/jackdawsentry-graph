import type { Edge, Node } from '@xyflow/react';

import type {
  ExpansionResponseV2,
  LayoutHints,
  NodeLayoutMetadata,
} from '../types/graph';
import type { NodePlacementDescriptor } from '../store/graphStore';
import { getNodeDimensions, type NodeDimensions } from './elkLayout';

const HORIZONTAL_GAP = 72;
const VERTICAL_GAP = 44;
const COLLISION_PADDING = 24;

type Rect = {
  x: number;
  y: number;
  width: number;
  height: number;
};

interface PlacementGraphNodeState {
  distance: number;
  side: -1 | 1;
}

interface CreateLocalNodePlacementsParams {
  existingNodes: Node[];
  newNodes: Node[];
  edges: Edge[];
  seedNodeId: string;
  operationType: ExpansionResponseV2['operation_type'];
  layoutHints?: LayoutHints;
  layoutToken: string;
  measuredSizes?: Map<string, NodeDimensions>;
}

interface ResolveNodeCollisionsParams {
  anchorNode: Node;
  existingNodes: Node[];
  nodesToPlace: Node[];
  initialPositions: Map<string, { x: number; y: number }>;
  measuredSizes?: Map<string, NodeDimensions>;
}

interface BuildLocalLayoutNeighborhoodParams {
  allNodes: Node[];
  allEdges: Edge[];
  pendingNodeIds: Set<string>;
  layoutMetaMap: Map<string, NodeLayoutMetadata>;
}

interface LocalLayoutNeighborhood {
  nodes: Node[];
  edges: Edge[];
  fixedPositions: Map<string, { x: number; y: number }>;
}

type MeasuredFlowNode = Node & {
  measured?: { width?: number; height?: number };
  width?: number;
  height?: number;
};

function hashString(value: string): number {
  let hash = 0;
  for (let i = 0; i < value.length; i++) {
    hash = (hash * 31 + value.charCodeAt(i)) >>> 0;
  }
  return hash;
}

function nodeDepth(node: Node): number {
  return ((node.data as { depth?: number } | undefined)?.depth) ?? 0;
}

function nodeLabel(node: Node): string {
  const data = (node.data as {
    display_label?: string;
    display_sublabel?: string;
    node_type?: string;
  } | undefined);

  return [
    `${nodeDepth(node)}`.padStart(4, '0'),
    data?.node_type ?? `${node.type ?? ''}`,
    data?.display_label ?? '',
    data?.display_sublabel ?? '',
    node.id,
  ].join(':');
}

function sortNodesDeterministically(nodes: Node[]): Node[] {
  return [...nodes].sort((left, right) => nodeLabel(left).localeCompare(nodeLabel(right)));
}

function rectsOverlap(a: Rect, b: Rect, padding = COLLISION_PADDING): boolean {
  return (
    a.x < b.x + b.width + padding
    && a.x + a.width + padding > b.x
    && a.y < b.y + b.height + padding
    && a.y + a.height + padding > b.y
  );
}

function rectForNode(
  node: Node,
  position: { x: number; y: number },
  measuredSizes?: Map<string, NodeDimensions>,
): Rect {
  const size = getNodeDimensions(node, measuredSizes);
  return {
    x: position.x,
    y: position.y,
    width: size.width,
    height: size.height,
  };
}

function collectOccupiedRects(
  nodes: Node[],
  measuredSizes?: Map<string, NodeDimensions>,
): Rect[] {
  return nodes.map((node) => rectForNode(node, node.position, measuredSizes));
}

function laneOffsets(preferredLane = 0, maxMagnitude = 18): number[] {
  const offsets = [preferredLane];
  for (let magnitude = 1; magnitude <= maxMagnitude; magnitude++) {
    offsets.push(preferredLane + magnitude, preferredLane - magnitude);
  }
  return offsets;
}

function buildAdjacency(
  seedNodeId: string,
  newNodes: Node[],
  edges: Edge[],
): Map<string, Set<string>> {
  const adjacency = new Map<string, Set<string>>();
  const relevantIds = new Set([seedNodeId, ...newNodes.map((node) => node.id)]);

  for (const nodeId of relevantIds) {
    adjacency.set(nodeId, new Set());
  }

  for (const edge of edges) {
    if (!relevantIds.has(edge.source) || !relevantIds.has(edge.target)) {
      continue;
    }
    adjacency.get(edge.source)?.add(edge.target);
    adjacency.get(edge.target)?.add(edge.source);
  }

  return adjacency;
}

function directNeighborSide(
  nodeId: string,
  seedNodeId: string,
  operationType: ExpansionResponseV2['operation_type'],
  edges: Edge[],
): -1 | 1 {
  if (operationType === 'expand_prev') {
    return -1;
  }
  if (operationType === 'expand_next') {
    return 1;
  }

  const directEdges = edges.filter(
    (edge) =>
      (edge.source === seedNodeId && edge.target === nodeId)
      || (edge.target === seedNodeId && edge.source === nodeId),
  );

  for (const edge of directEdges) {
    if (edge.source === seedNodeId && edge.target === nodeId) {
      return 1;
    }
    if (edge.target === seedNodeId && edge.source === nodeId) {
      return -1;
    }
  }

  return hashString(nodeId) % 2 === 0 ? 1 : -1;
}

function derivePlacementGraph(
  seedNodeId: string,
  newNodes: Node[],
  edges: Edge[],
  operationType: ExpansionResponseV2['operation_type'],
): Map<string, PlacementGraphNodeState> {
  const adjacency = buildAdjacency(seedNodeId, newNodes, edges);
  const states = new Map<string, PlacementGraphNodeState>();
  const visited = new Set<string>([seedNodeId]);
  const queue: string[] = [seedNodeId];

  while (queue.length > 0) {
    const currentId = queue.shift() ?? seedNodeId;
    const currentState = states.get(currentId);
    const neighbors = [...(adjacency.get(currentId) ?? [])].sort();

    for (const neighborId of neighbors) {
      if (visited.has(neighborId)) continue;
      visited.add(neighborId);
      const state: PlacementGraphNodeState = currentId === seedNodeId
        ? {
            distance: 1,
            side: directNeighborSide(neighborId, seedNodeId, operationType, edges),
          }
        : {
            distance: (currentState?.distance ?? 0) + 1,
            side: currentState?.side ?? 1,
          };

      states.set(neighborId, state);
      queue.push(neighborId);
    }
  }

  for (const node of sortNodesDeterministically(newNodes)) {
    if (states.has(node.id)) continue;
    states.set(node.id, {
      distance: 1,
      side:
        operationType === 'expand_prev'
          ? -1
          : operationType === 'expand_next'
            ? 1
            : hashString(node.id) % 2 === 0 ? 1 : -1,
    });
  }

  return states;
}

function chooseAnchorNode(
  existingNodes: Node[],
  seedNodeId: string,
  layoutHints?: LayoutHints,
): Node | null {
  const preferredIds = [
    ...(layoutHints?.anchor_node_ids ?? []),
    seedNodeId,
  ];

  for (const nodeId of preferredIds) {
    const candidate = existingNodes.find((node) => node.id === nodeId);
    if (candidate) return candidate;
  }

  return existingNodes.find((node) => node.id === seedNodeId) ?? existingNodes[0] ?? null;
}

function findOpenPosition(
  params: {
    anchorRect: Rect;
    node: Node;
    distance: number;
    side: -1 | 1;
    preferredLane: number;
    occupiedRects: Rect[];
    measuredSizes?: Map<string, NodeDimensions>;
  },
): { x: number; y: number } {
  const { anchorRect, node, distance, side, preferredLane, occupiedRects, measuredSizes } = params;
  const size = getNodeDimensions(node, measuredSizes);

  for (let outwardStep = 0; outwardStep < 8; outwardStep++) {
    const columnDistance = distance + outwardStep;
    const columnStep = Math.max(anchorRect.width, size.width, 280) + HORIZONTAL_GAP;
    const x = side === 1
      ? anchorRect.x + anchorRect.width + HORIZONTAL_GAP + (columnDistance - 1) * columnStep
      : anchorRect.x - size.width - HORIZONTAL_GAP - (columnDistance - 1) * columnStep;

    const centerAlignedY = anchorRect.y + (anchorRect.height - size.height) / 2;
    for (const lane of laneOffsets(preferredLane)) {
      const y = centerAlignedY + lane * (size.height + VERTICAL_GAP);
      const rect = { x, y, width: size.width, height: size.height };
      if (!occupiedRects.some((occupied) => rectsOverlap(rect, occupied))) {
        occupiedRects.push(rect);
        return { x, y };
      }
    }
  }

  const fallbackX = side === 1
    ? anchorRect.x + anchorRect.width + HORIZONTAL_GAP + distance * 320
    : anchorRect.x - size.width - HORIZONTAL_GAP - distance * 320;
  const fallbackY = anchorRect.y + (anchorRect.height - size.height) / 2;
  return { x: fallbackX, y: fallbackY };
}

export function createLocalNodePlacements(
  params: CreateLocalNodePlacementsParams,
): Map<string, NodePlacementDescriptor> {
  const {
    existingNodes,
    newNodes,
    edges,
    seedNodeId,
    operationType,
    layoutHints,
    layoutToken,
    measuredSizes,
  } = params;

  if (newNodes.length === 0) {
    return new Map();
  }

  const anchorNode = chooseAnchorNode(existingNodes, seedNodeId, layoutHints);
  const anchorRect = anchorNode
    ? rectForNode(anchorNode, anchorNode.position, measuredSizes)
    : { x: 0, y: 0, width: 320, height: 160 };
  const occupiedRects = collectOccupiedRects(existingNodes, measuredSizes);
  const graphState = derivePlacementGraph(seedNodeId, newNodes, edges, operationType);
  const groupedNodes = new Map<string, Node[]>();

  for (const node of sortNodesDeterministically(newNodes)) {
    const state = graphState.get(node.id) ?? { distance: 1, side: 1 };
    const key = `${state.side}:${state.distance}`;
    const group = groupedNodes.get(key) ?? [];
    group.push(node);
    groupedNodes.set(key, group);
  }

  const placements = new Map<string, NodePlacementDescriptor>();
  for (const node of sortNodesDeterministically(newNodes)) {
    const state = graphState.get(node.id) ?? { distance: 1, side: 1 };
    const key = `${state.side}:${state.distance}`;
    const group = groupedNodes.get(key) ?? [node];
    const preferredLane = group.findIndex((candidate) => candidate.id === node.id);
    const position = findOpenPosition({
      anchorRect,
      node,
      distance: state.distance,
      side: state.side,
      preferredLane,
      occupiedRects,
      measuredSizes,
    });

    placements.set(node.id, {
      position,
      layoutMeta: {
        anchorNodeId: anchorNode?.id ?? seedNodeId,
        lastLayoutToken: layoutToken,
        layoutLocked: false,
        userPlaced: false,
        // expand_prev nodes are placed to the LEFT by incremental logic; ELK's
        // interactive layered algorithm can't reliably place free nodes to the
        // left of a fixed seed, so mark these as final to skip the ELK pass.
        placementSource: operationType === 'expand_prev' ? 'elk_refinement' : 'local_expansion',
      },
    });
  }

  return placements;
}

export function resolveNodeCollisions(
  params: ResolveNodeCollisionsParams,
): Map<string, { x: number; y: number }> {
  const { anchorNode, existingNodes, nodesToPlace, initialPositions, measuredSizes } = params;
  const anchorRect = rectForNode(anchorNode, anchorNode.position, measuredSizes);
  const occupiedRects = collectOccupiedRects(
    existingNodes.filter((node) => !initialPositions.has(node.id)),
    measuredSizes,
  );
  const resolvedPositions = new Map<string, { x: number; y: number }>();

  for (const node of sortNodesDeterministically(nodesToPlace)) {
    const preferredPosition = initialPositions.get(node.id) ?? node.position;
    const nodeRect = rectForNode(node, preferredPosition, measuredSizes);
    if (!occupiedRects.some((occupied) => rectsOverlap(nodeRect, occupied))) {
      occupiedRects.push(nodeRect);
      resolvedPositions.set(node.id, preferredPosition);
      continue;
    }

    const nodeCenterX = nodeRect.x + nodeRect.width / 2;
    const anchorCenterX = anchorRect.x + anchorRect.width / 2;
    const nodeCenterY = nodeRect.y + nodeRect.height / 2;
    const anchorCenterY = anchorRect.y + anchorRect.height / 2;
    const side: -1 | 1 = nodeCenterX >= anchorCenterX ? 1 : -1;
    const distance = Math.max(
      1,
      Math.round(Math.abs(nodeCenterX - anchorCenterX) / (Math.max(anchorRect.width, nodeRect.width) + HORIZONTAL_GAP)),
    );
    const preferredLane = Math.round(
      (nodeCenterY - anchorCenterY) / (nodeRect.height + VERTICAL_GAP),
    );

    const resolvedPosition = findOpenPosition({
      anchorRect,
      node,
      distance,
      side,
      preferredLane,
      occupiedRects,
      measuredSizes,
    });
    resolvedPositions.set(node.id, resolvedPosition);
  }

  return resolvedPositions;
}

export function buildLocalLayoutNeighborhood(
  params: BuildLocalLayoutNeighborhoodParams,
): LocalLayoutNeighborhood {
  const { allNodes, allEdges, pendingNodeIds, layoutMetaMap } = params;
  const relevantNodeIds = new Set<string>(pendingNodeIds);

  for (const nodeId of pendingNodeIds) {
    const anchorNodeId = layoutMetaMap.get(nodeId)?.anchorNodeId;
    if (anchorNodeId) {
      relevantNodeIds.add(anchorNodeId);
    }
  }

  for (const edge of allEdges) {
    if (pendingNodeIds.has(edge.source) || pendingNodeIds.has(edge.target)) {
      relevantNodeIds.add(edge.source);
      relevantNodeIds.add(edge.target);
    }
  }

  const nodes = allNodes.filter((node) => relevantNodeIds.has(node.id));
  const edges = allEdges.filter(
    (edge) => relevantNodeIds.has(edge.source) && relevantNodeIds.has(edge.target),
  );
  const fixedPositions = new Map<string, { x: number; y: number }>();

  for (const node of nodes) {
    if (!pendingNodeIds.has(node.id)) {
      fixedPositions.set(node.id, node.position);
    }
  }

  return { nodes, edges, fixedPositions };
}

export function collectMeasuredNodeSizes(nodes: Node[]): Map<string, NodeDimensions> {
  const measuredSizes = new Map<string, NodeDimensions>();

  for (const node of nodes as MeasuredFlowNode[]) {
    const width = node.measured?.width ?? node.width;
    const height = node.measured?.height ?? node.height;
    if (
      typeof width === 'number'
      && width > 0
      && typeof height === 'number'
      && height > 0
    ) {
      measuredSizes.set(node.id, { width, height });
    }
  }

  return measuredSizes;
}

/**
 * Returns true when a node with the given layout metadata should enter the
 * ELK refinement pass in InvestigationGraph.
 *
 * Only nodes that arrived via incremental placement (`'local_expansion'`) are
 * eligible.  Nodes already assigned a final source (`'elk_refinement'`,
 * `'manual_drag'`, `'snapshot_restore'`, etc.) are skipped — in particular,
 * `expand_prev` nodes are pre-marked as `'elk_refinement'` so that ELK's
 * interactive layered algorithm cannot pull them from their correct left-side
 * positions to the right of the anchor.
 */
export function isEligibleForElkRefinement(meta: NodeLayoutMetadata): boolean {
  return meta.placementSource === 'local_expansion';
}
