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

import ELK from 'elkjs/lib/main.js';
import ElkWorker from 'elkjs/lib/elk-worker.min.js?worker';
import type { Node, Edge } from '@xyflow/react';

// ELK layout runs in a Web Worker — never blocks the main thread.
// workerFactory is called by elkjs/lib/main.js; we use Vite's pre-bundled
// worker class so the worker URL is content-hashed at build time.
const elk = new ELK({
  workerFactory: () => new ElkWorker(),
});

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
  fixedPositions?: Map<string, { x: number; y: number }>,
): Promise<Map<string, { x: number; y: number }>> {
  if (nodes.length === 0) return new Map();

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
      return fixed
        ? { id: n.id, width: NODE_WIDTH, height: NODE_HEIGHT, x: fixed.x, y: fixed.y }
        : { id: n.id, width: NODE_WIDTH, height: NODE_HEIGHT };
    }),
    edges: edges.map((e) => ({
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
