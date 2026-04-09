/**
 * ELK Layered layout for the investigation graph.
 *
 * Layout runs in a Web Worker so it never blocks the main thread.
 * `elkjs/lib/main.js` accepts a `workerFactory` callback; Vite's `?worker`
 * suffix pre-bundles `elk-worker.min.js` as a Worker asset and gives us a
 * constructor for it.  The `web-worker` Node.js package referenced inside
 * `main.js` is aliased to an empty stub in `vite.config.ts` so Rolldown does
 * not fail to resolve it in the browser build.
 *
 * ELK options are tuned for a left-to-right directed fund-flow investigation
 * graph: layered algorithm, wide spacing to accommodate node cards.
 */

import ELK from 'elkjs/lib/elk-api.js';
import ElkWorker from 'elkjs/lib/elk-worker.min.js?worker';
import type { Node, Edge } from '@xyflow/react';

// ELK layout runs in a Web Worker — never blocks the main thread.
// We provide workerFactory explicitly and use Vite's pre-bundled worker class
// so the worker URL is content-hashed at build time. Import elk-api.js
// directly to avoid bundling elkjs's fake-worker fallback, which otherwise
// duplicates the full worker payload inside the main app chunk.
const elk = new ELK({
  workerFactory: () => new ElkWorker(),
});

const ELK_OPTIONS: Record<string, string> = {
  'elk.algorithm': 'layered',
  'elk.direction': 'RIGHT',
  'elk.layered.spacing.nodeNodeBetweenLayers': '170',
  'elk.spacing.nodeNode': '96',
  'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
  'elk.layered.nodePlacement.strategy': 'BRANDES_KOEPF',
};

export interface NodeDimensions {
  width: number;
  height: number;
}

export interface ComputeElkLayoutOptions {
  fixedPositions?: Map<string, { x: number; y: number }>;
  measuredSizes?: Map<string, NodeDimensions>;
}

function fallbackNodeDimensions(node: Node): NodeDimensions {
  const nodeType = `${node.type ?? (node.data as { node_type?: string } | undefined)?.node_type ?? ''}`;

  switch (nodeType) {
    case 'address':
      return { width: 320, height: 160 };
    case 'entity':
    case 'service':
      return { width: 300, height: 160 };
    case 'bridge_hop':
      return { width: 300, height: 180 };
    case 'swap_event':
      return { width: 290, height: 165 };
    case 'lightning_channel_open':
    case 'lightning_channel_close':
    case 'atomic_swap':
      return { width: 300, height: 175 };
    case 'btc_sidechain_peg_in':
    case 'btc_sidechain_peg_out':
    case 'solana_instruction':
    case 'utxo':
      return { width: 280, height: 160 };
    case 'cluster_summary':
      return { width: 220, height: 120 };
    default:
      return { width: 260, height: 150 };
  }
}

export function getNodeDimensions(
  node: Node,
  measuredSizes?: Map<string, NodeDimensions>,
): NodeDimensions {
  return measuredSizes?.get(node.id) ?? fallbackNodeDimensions(node);
}

/**
 * Compute ELK Layered positions for the given React Flow nodes and edges.
 *
 * @param nodes           All nodes in the current graph (new + existing).
 * @param edges           All edges in the current graph.
 * @param fixedPositions  Positions of nodes that must not move.  When provided,
 *                        interactive layout is enabled so that ELK keeps these
 *                        nodes in their current layer positions and only freely
 *                        places nodes absent from this map.
 * @returns Position map `{ nodeId → { x, y } }` for all nodes.
 */
export async function computeElkLayout(
  nodes: Node[],
  edges: Edge[],
  options?: ComputeElkLayoutOptions,
): Promise<Map<string, { x: number; y: number }>> {
  if (nodes.length === 0) return new Map();
  const nodeIds = new Set(nodes.map((node) => node.id));
  const layoutEdges = edges.filter(
    (edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target),
  );
  const fixedPositions = options?.fixedPositions;

  const hasFixed = fixedPositions && fixedPositions.size > 0;

  // When some nodes are already placed, enable ELK's interactive mode so it
  // honours their layer assignments and only moves the newly added nodes.
  const layoutOptions: Record<string, string> = hasFixed
    ? { ...ELK_OPTIONS, 'elk.interactiveLayout': 'true' }
    : ELK_OPTIONS;

  const graph = {
    id: 'root',
    layoutOptions,
    children: nodes.map((n) => {
      const fixed = fixedPositions?.get(n.id);
      const { width, height } = getNodeDimensions(n, options?.measuredSizes);
      return fixed
        ? { id: n.id, width, height, x: fixed.x, y: fixed.y }
        : { id: n.id, width, height };
    }),
    edges: layoutEdges.map((e) => ({
      id: e.id,
      sources: [e.source],
      targets: [e.target],
    })),
  };

  let result;
  try {
    result = await elk.layout(graph);
  } catch (err) {
    console.error('ELK layout failed:', err);
    return new Map();
  }

  const positions = new Map<string, { x: number; y: number }>();
  for (const child of result.children ?? []) {
    if (child.x !== undefined && child.y !== undefined) {
      positions.set(child.id, { x: child.x, y: child.y });
    }
  }
  return positions;
}
