/**
 * ELK Layered layout for the investigation graph.
 *
 * Runs ELK in the main thread (a Web Worker version can be introduced later
 * if layout time exceeds ~100ms on large graphs).
 *
 * ELK options are tuned for a left-to-right directed fund-flow investigation
 * graph: layered algorithm, wide spacing to accommodate node cards.
 */

// Use the browser-safe ELK bundle (no web-worker dependency)
import ELK from 'elkjs/lib/elk.bundled.js';
import type { Node, Edge } from '@xyflow/react';

const elk = new ELK();

const ELK_OPTIONS: Record<string, string> = {
  'elk.algorithm': 'layered',
  'elk.direction': 'RIGHT',
  'elk.layered.spacing.nodeNodeBetweenLayers': '120',
  'elk.spacing.nodeNode': '60',
  'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
  'elk.layered.nodePlacement.strategy': 'BRANDES_KOEPF',
};

/** Default node dimensions used for layout (match CSS of custom node components) */
const NODE_WIDTH = 180;
const NODE_HEIGHT = 64;

/**
 * Compute ELK Layered positions for the given React Flow nodes and edges.
 * Returns a position map `{ nodeId → { x, y } }`.
 */
export async function computeElkLayout(
  nodes: Node[],
  edges: Edge[],
): Promise<Map<string, { x: number; y: number }>> {
  if (nodes.length === 0) return new Map();

  const graph = {
    id: 'root',
    layoutOptions: ELK_OPTIONS,
    children: nodes.map((n) => ({
      id: n.id,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    })),
    edges: edges.map((e) => ({
      id: e.id,
      sources: [e.source],
      targets: [e.target],
    })),
  };

  const result = await elk.layout(graph);

  const positions = new Map<string, { x: number; y: number }>();
  for (const child of result.children ?? []) {
    if (child.x !== undefined && child.y !== undefined) {
      positions.set(child.id, { x: child.x, y: child.y });
    }
  }
  return positions;
}
