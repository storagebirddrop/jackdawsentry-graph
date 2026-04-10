// @vitest-environment jsdom
/**
 * Unit tests for graphStore state actions that relate to layout stability.
 *
 * jsdom environment is required because @xyflow/react uses browser globals
 * at module initialisation time.
 *
 * Tests cover the three critical behaviours introduced/fixed in Pass 2–3:
 *   1. applyExpansionDelta preserves existing node positions.
 *   2. applyExpansionDelta preserves userPlaced across delta rebuilds (H1 fix).
 *   3. markUserPlaced sets the flag on the correct node only.
 *   4. New nodes arrive at {0,0} before setRfPositions is called.
 *   5. setRfPositions updates only the specified nodes.
 *   6. updateBridgeHopStatus preserves userPlaced (H1-NEW fix).
 */

import { describe, expect, it, beforeEach } from 'vitest';
import { useGraphStore } from './graphStore';
import type { InvestigationEdge, InvestigationNode, ExpansionResponseV2, BridgeHopStatusResponse } from '../types/graph';

// ---------------------------------------------------------------------------
// Minimal fixtures
// ---------------------------------------------------------------------------

function makeNode(
  id: string,
  branchId = 'branch-a',
  overrides: Partial<InvestigationNode> = {},
): InvestigationNode {
  return {
    node_id: id,
    node_type: 'address',
    branch_id: branchId,
    path_id: 'path-1',
    lineage_id: `lineage-${id}`,
    depth: 0,
    expandable_directions: ['next'],
    ...overrides,
  };
}

function makeScopedAddressNode(
  id: string,
  chain: string,
  overrides: Partial<InvestigationNode> = {},
): InvestigationNode {
  const address = id.split(':').slice(2).join(':') || id;
  return makeNode(id, 'branch-a', {
    chain,
    address_data: {
      address,
      chain,
    },
    ...overrides,
  });
}

function makeEntityNode(id: string): InvestigationNode {
  return {
    node_id: id,
    node_type: 'entity',
    branch_id: 'branch-a',
    path_id: `path-${id}`,
    lineage_id: `lineage-${id}`,
    depth: 0,
    expandable_directions: [],
  };
}

function makeDelta(nodes: InvestigationNode[]): ExpansionResponseV2 {
  return {
    session_id: 'sess-1',
    branch_id: 'branch-a',
    operation_id: 'op-1',
    operation_type: 'expand_next',
    nodes,
    edges: [],
    layout_hints: { suggested_layout: 'layered' },
    chain_context: { primary_chain: 'ethereum', chains_present: ['ethereum'] },
  };
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

const SEED = makeNode('seed');

beforeEach(() => {
  useGraphStore.getState().reset();
  useGraphStore.getState().initSession('sess-1', SEED);
});

// ---------------------------------------------------------------------------
// R2: applyExpansionDelta preserves existing positions
// ---------------------------------------------------------------------------

describe('applyExpansionDelta — position preservation', () => {
  it('existing node positions are not reset to {0,0} on delta', () => {
    // Manually place the seed node at a known position
    useGraphStore.getState().setRfPositions(new Map([['seed', { x: 150, y: 300 }]]));

    // Apply a delta that introduces a new node (seed remains unchanged)
    useGraphStore.getState().applyExpansionDelta(makeDelta([makeNode('n1')]));

    const rfNodes = useGraphStore.getState().rfNodes;
    const seedRf = rfNodes.find((n) => n.id === 'seed');
    expect(seedRf?.position).toEqual({ x: 150, y: 300 });
  });

  it('new nodes arrive at position {0,0} before setRfPositions is called', () => {
    useGraphStore.getState().applyExpansionDelta(makeDelta([makeNode('n1')]));

    const rfNodes = useGraphStore.getState().rfNodes;
    const n1Rf = rfNodes.find((n) => n.id === 'n1');
    expect(n1Rf?.position).toEqual({ x: 0, y: 0 });
  });
});

// ---------------------------------------------------------------------------
// H1 fix: userPlaced preserved across delta rebuilds
// ---------------------------------------------------------------------------

describe('applyExpansionDelta — userPlaced flag preservation (H1 fix)', () => {
  it('userPlaced is preserved on an existing node after a subsequent delta', () => {
    // Mark the seed node as user-placed
    useGraphStore.getState().markUserPlaced('seed');

    // Verify it was set
    let rfNodes = useGraphStore.getState().rfNodes;
    let seedRf = rfNodes.find((n) => n.id === 'seed');
    expect((seedRf?.data as { userPlaced?: boolean }).userPlaced).toBe(true);

    // Apply a delta that adds a new node — this triggers toRfNode() rebuild
    useGraphStore.getState().applyExpansionDelta(makeDelta([makeNode('n1')]));

    // userPlaced must survive the rebuild
    rfNodes = useGraphStore.getState().rfNodes;
    seedRf = rfNodes.find((n) => n.id === 'seed');
    expect((seedRf?.data as { userPlaced?: boolean }).userPlaced).toBe(true);
  });

  it('userPlaced is NOT set on nodes that were not manually placed', () => {
    useGraphStore.getState().markUserPlaced('seed');
    useGraphStore.getState().applyExpansionDelta(makeDelta([makeNode('n1')]));

    const rfNodes = useGraphStore.getState().rfNodes;
    const n1Rf = rfNodes.find((n) => n.id === 'n1');
    expect((n1Rf?.data as { userPlaced?: boolean }).userPlaced).toBeFalsy();
  });
});

// ---------------------------------------------------------------------------
// markUserPlaced
// ---------------------------------------------------------------------------

describe('markUserPlaced', () => {
  it('sets userPlaced = true on the target node', () => {
    useGraphStore.getState().markUserPlaced('seed');

    const rfNodes = useGraphStore.getState().rfNodes;
    const seedRf = rfNodes.find((n) => n.id === 'seed');
    expect((seedRf?.data as { userPlaced?: boolean }).userPlaced).toBe(true);
  });

  it('does not affect other nodes', () => {
    // Add a second node first
    useGraphStore.getState().applyExpansionDelta(makeDelta([makeNode('n1')]));
    useGraphStore.getState().markUserPlaced('seed');

    const rfNodes = useGraphStore.getState().rfNodes;
    const n1Rf = rfNodes.find((n) => n.id === 'n1');
    expect((n1Rf?.data as { userPlaced?: boolean }).userPlaced).toBeFalsy();
  });
});

// ---------------------------------------------------------------------------
// setRfPositions
// ---------------------------------------------------------------------------

describe('setRfPositions', () => {
  it('updates only the specified nodes and leaves others unchanged', () => {
    useGraphStore.getState().applyExpansionDelta(makeDelta([makeNode('n1')]));

    useGraphStore.getState().setRfPositions(new Map([['seed', { x: 100, y: 200 }]]));

    const rfNodes = useGraphStore.getState().rfNodes;
    const seedRf = rfNodes.find((n) => n.id === 'seed');
    const n1Rf = rfNodes.find((n) => n.id === 'n1');

    expect(seedRf?.position).toEqual({ x: 100, y: 200 });
    expect(n1Rf?.position).toEqual({ x: 0, y: 0 }); // unchanged
  });
});

// ---------------------------------------------------------------------------
// updateBridgeHopStatus — userPlaced preservation (H1-NEW fix)
// ---------------------------------------------------------------------------

function makeBridgeHopNode(id: string): InvestigationNode {
  return {
    node_id: id,
    node_type: 'bridge_hop',
    branch_id: 'branch-a',
    path_id: 'path-1',
    lineage_id: `lineage-${id}`,
    depth: 0,
    expandable_directions: [],
    bridge_hop_data: {
      hop_id: id,
      protocol_id: 'test-bridge',
      mechanism: 'lock-mint',
      source_chain: 'ethereum',
      status: 'pending',
      correlation_confidence: 0.9,
    },
  };
}

function makeStatusUpdate(hopId: string): BridgeHopStatusResponse {
  return {
    hop_id: hopId,
    status: 'completed',
    destination_chain: 'polygon',
    destination_tx_hash: '0xabc',
  };
}

describe('updateBridgeHopStatus — userPlaced flag preservation (H1-NEW fix)', () => {
  it('preserves userPlaced on a bridge_hop node after a status update', () => {
    const hopNode = makeBridgeHopNode('hop1');
    useGraphStore.getState().applyExpansionDelta(makeDelta([hopNode]));
    useGraphStore.getState().markUserPlaced('hop1');

    // Confirm the flag is set before the update
    let rf = useGraphStore.getState().rfNodes.find((n) => n.id === 'hop1');
    expect((rf?.data as { userPlaced?: boolean }).userPlaced).toBe(true);

    // Status poll fires — should not clear userPlaced
    useGraphStore.getState().updateBridgeHopStatus('hop1', makeStatusUpdate('hop1'));

    rf = useGraphStore.getState().rfNodes.find((n) => n.id === 'hop1');
    expect((rf?.data as { userPlaced?: boolean }).userPlaced).toBe(true);
  });

  it('does not set userPlaced on a bridge_hop node that was never dragged', () => {
    const hopNode = makeBridgeHopNode('hop2');
    useGraphStore.getState().applyExpansionDelta(makeDelta([hopNode]));

    useGraphStore.getState().updateBridgeHopStatus('hop2', makeStatusUpdate('hop2'));

    const rf = useGraphStore.getState().rfNodes.find((n) => n.id === 'hop2');
    expect((rf?.data as { userPlaced?: boolean }).userPlaced).toBeFalsy();
  });
});

// ---------------------------------------------------------------------------
// pendingPreview lifecycle (V1 selective expansion)
// ---------------------------------------------------------------------------

describe('pendingPreview — lifecycle and isolation', () => {
  it('setPendingPreview stores the response', () => {
    const preview = makeDelta([makeNode('prev-node')]);
    useGraphStore.getState().setPendingPreview(preview);
    expect(useGraphStore.getState().pendingPreview).toBe(preview);
  });

  it('setPendingPreview(null) clears the stored response', () => {
    useGraphStore.getState().setPendingPreview(makeDelta([makeNode('prev-node')]));
    useGraphStore.getState().setPendingPreview(null);
    expect(useGraphStore.getState().pendingPreview).toBeNull();
  });

  it('reset() clears pendingPreview — F-2 regression', () => {
    // Set a preview, then reset the session.
    useGraphStore.getState().setPendingPreview(makeDelta([makeNode('prev-node')]));
    expect(useGraphStore.getState().pendingPreview).not.toBeNull();

    useGraphStore.getState().reset();

    expect(useGraphStore.getState().pendingPreview).toBeNull();
  });

  it('reset() clears pendingPreview alongside all other session state', () => {
    useGraphStore.getState().setPendingPreview(makeDelta([makeNode('prev-node')]));

    useGraphStore.getState().reset();

    const state = useGraphStore.getState();
    expect(state.sessionId).toBeNull();
    expect(state.nodeMap.size).toBe(0);
    expect(state.rfNodes).toHaveLength(0);
    expect(state.pendingPreview).toBeNull();
  });

  it('exportSnapshot excludes pendingPreview', () => {
    useGraphStore.getState().setPendingPreview(makeDelta([makeNode('prev-node')]));

    const json = useGraphStore.getState().exportSnapshot();
    const parsed = JSON.parse(json) as Record<string, unknown>;

    expect('pendingPreview' in parsed).toBe(false);
  });

  it('importSnapshot does not restore pendingPreview even if the JSON contains it', () => {
    // Build a snapshot that (hypothetically) contains pendingPreview.
    const snapshot = JSON.stringify({
      sessionId: 'sess-restore',
      nodes: [],
      edges: [],
      positions: {},
      branches: [],
      pendingPreview: makeDelta([makeNode('stale-preview')]),
    });

    // Ensure there is no active preview before import.
    useGraphStore.getState().setPendingPreview(null);
    const ok = useGraphStore.getState().importSnapshot(snapshot);

    expect(ok).toBe(true);
    expect(useGraphStore.getState().pendingPreview).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// V2 subset apply — applyExpansionDelta with synthetically filtered response
//
// handleApplyPreview (InvestigationGraph.tsx) is not directly callable from
// store tests.  Its observable output is a call to applyExpansionDelta() with
// a synthetically constructed ExpansionResponseV2 subset.  These tests
// replicate the exact filtering logic (T-V2-1 through T-V2-4) and verify the
// store state that results.
// ---------------------------------------------------------------------------

function makeEdge(
  edgeId: string,
  sourceNodeId: string,
  targetNodeId: string,
): InvestigationEdge {
  return {
    edge_id: edgeId,
    edge_type: 'transfer',
    source_node_id: sourceNodeId,
    target_node_id: targetNodeId,
    direction: 'forward',
    branch_id: 'branch-a',
  };
}

function makePreviewWithEdges(
  nodes: InvestigationNode[],
  edges: InvestigationEdge[],
): ExpansionResponseV2 {
  return {
    session_id: 'sess-1',
    branch_id: 'branch-a',
    operation_id: 'op-preview',
    operation_type: 'expand_next',
    seed_node_id: 'seed',
    nodes,
    edges,
    layout_hints: { suggested_layout: 'layered' },
    chain_context: { primary_chain: 'ethereum', chains_present: ['ethereum'] },
  };
}

/** Replicate handleApplyPreview's subset construction for a given selection. */
function buildSubsetResponse(
  preview: ExpansionResponseV2,
  selectedEdgeIds: Set<string>,
): ExpansionResponseV2 {
  const allEdges = preview.edges ?? preview.added_edges ?? [];
  const allNodes = preview.nodes ?? preview.added_nodes ?? [];
  const filteredEdges = allEdges.filter((e) => selectedEdgeIds.has(e.edge_id));
  const referencedNodeIds = new Set(filteredEdges.flatMap((e) => [e.source_node_id, e.target_node_id]));
  const filteredNodes = allNodes.filter((n) => referencedNodeIds.has(n.node_id));
  return { ...preview, nodes: filteredNodes, edges: filteredEdges, added_nodes: filteredNodes, added_edges: filteredEdges };
}

describe('V2 subset apply — applyExpansionDelta with filtered response', () => {
  it('T-V2-1: applies only selected edges and their referenced nodes', () => {
    const n1 = makeNode('n1');
    const n2 = makeNode('n2');
    const e1 = makeEdge('e1', 'seed', 'n1');
    const e2 = makeEdge('e2', 'seed', 'n2');

    const preview = makePreviewWithEdges([n1, n2], [e1, e2]);
    // Analyst selects only e1 — n2 and e2 must not land on canvas
    const subset = buildSubsetResponse(preview, new Set(['e1']));

    useGraphStore.getState().applyExpansionDelta(subset);

    const { nodeMap, edgeMap } = useGraphStore.getState();
    expect(nodeMap.has('n1')).toBe(true);
    expect(nodeMap.has('n2')).toBe(false);   // not referenced by any selected edge
    expect(edgeMap.has('e1')).toBe(true);
    expect(edgeMap.has('e2')).toBe(false);   // not selected
  });

  it('T-V2-2: excludes nodes not referenced by any selected edge', () => {
    const n1 = makeNode('n1');
    const n2 = makeNode('n2');
    const n3 = makeNode('n3');
    const e1 = makeEdge('e1', 'seed', 'n1');
    const e2 = makeEdge('e2', 'seed', 'n2');
    const e3 = makeEdge('e3', 'seed', 'n3');

    const preview = makePreviewWithEdges([n1, n2, n3], [e1, e2, e3]);
    // Select e1 and e2 only — n3 and e3 must be excluded
    const subset = buildSubsetResponse(preview, new Set(['e1', 'e2']));

    useGraphStore.getState().applyExpansionDelta(subset);

    const { nodeMap, edgeMap } = useGraphStore.getState();
    expect(nodeMap.has('n1')).toBe(true);
    expect(nodeMap.has('n2')).toBe(true);
    expect(nodeMap.has('n3')).toBe(false);   // no selected edge references n3
    expect(edgeMap.has('e3')).toBe(false);
  });

  it('T-V2-3: pre-existing canvas nodes are not position-reset by a subset apply', () => {
    // Place n1 on canvas at a known position before the preview
    useGraphStore.getState().applyExpansionDelta(makeDelta([makeNode('n1')]));
    useGraphStore.getState().setRfPositions(new Map([['n1', { x: 500, y: 200 }]]));

    const rfBefore = useGraphStore.getState().rfNodes.find((n) => n.id === 'n1');
    expect(rfBefore?.position).toEqual({ x: 500, y: 200 });

    // Subset response references n1 (as an edge endpoint) alongside a new node n2
    const n2 = makeNode('n2');
    const e = makeEdge('e-n1n2', 'n1', 'n2');
    const preview = makePreviewWithEdges([makeNode('n1'), n2], [e]);
    const subset = buildSubsetResponse(preview, new Set(['e-n1n2']));

    useGraphStore.getState().applyExpansionDelta(subset);

    // n1 must not have been replaced — position must be preserved
    const rfAfter = useGraphStore.getState().rfNodes.find((n) => n.id === 'n1');
    expect(rfAfter?.position).toEqual({ x: 500, y: 200 });
    // n2 was genuinely new and was added
    expect(useGraphStore.getState().nodeMap.has('n2')).toBe(true);
  });

  it('T-V2-4: apply all (undefined selectedEdgeIds) is equivalent to applying the full preview', () => {
    const n1 = makeNode('n1');
    const n2 = makeNode('n2');
    const e1 = makeEdge('e1', 'seed', 'n1');
    const e2 = makeEdge('e2', 'seed', 'n2');

    // No subset filtering — pass the full preview directly (V1 / apply-all path)
    const preview = makePreviewWithEdges([n1, n2], [e1, e2]);
    useGraphStore.getState().applyExpansionDelta(preview);

    const { nodeMap, edgeMap } = useGraphStore.getState();
    expect(nodeMap.has('n1')).toBe(true);
    expect(nodeMap.has('n2')).toBe(true);
    expect(edgeMap.has('e1')).toBe(true);
    expect(edgeMap.has('e2')).toBe(true);
  });
});

describe('nodeAssetScopes snapshot and pruning', () => {
  const ROOT = makeScopedAddressNode('ethereum:address:0xaaa', 'ethereum');
  const SOLANA_NODE = makeScopedAddressNode(
    'solana:address:So11111111111111111111111111111111111111112',
    'solana',
  );
  const BITCOIN_NODE = makeScopedAddressNode(
    'bitcoin:address:bc1qexampleaddress0000000000000000000000000',
    'bitcoin',
  );
  const ENTITY_NODE = makeEntityNode('entity:binance');
  const ETH_USDC_SELECTOR = {
    mode: 'asset' as const,
    chain: 'ethereum',
    chain_asset_id: '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
    asset_symbol: 'USDC',
  };

  beforeEach(() => {
    useGraphStore.getState().reset();
    useGraphStore.getState().initSession('sess-scope', ROOT);
    useGraphStore.getState().applyExpansionDelta(makeDelta([
      SOLANA_NODE,
      BITCOIN_NODE,
      ENTITY_NODE,
    ]));
  });

  it('exportSnapshot and importSnapshot round-trip nodeAssetScopes and preserve empty-vs-missing semantics', () => {
    useGraphStore.getState().setNodeAssetScope(ROOT.node_id, [
      { ...ETH_USDC_SELECTOR, chain: 'Ethereum' },
      ETH_USDC_SELECTOR,
    ]);
    useGraphStore.getState().setNodeAssetScope(SOLANA_NODE.node_id, []);

    const snapshot = useGraphStore.getState().exportSnapshot();

    useGraphStore.getState().reset();
    const restored = useGraphStore.getState().importSnapshot(snapshot);

    expect(restored).toBe(true);
    const scopes = useGraphStore.getState().nodeAssetScopes;
    expect(scopes.has(ROOT.node_id)).toBe(true);
    expect(scopes.get(ROOT.node_id)).toEqual([ETH_USDC_SELECTOR]);
    expect(scopes.has(SOLANA_NODE.node_id)).toBe(true);
    expect(scopes.get(SOLANA_NODE.node_id)).toEqual([]);
    expect(scopes.has(BITCOIN_NODE.node_id)).toBe(false);
  });

  it('applyExpansionDelta prunes stored nodeAssetScopes for removed nodes', () => {
    useGraphStore.getState().setNodeAssetScope(SOLANA_NODE.node_id, []);

    useGraphStore.getState().applyExpansionDelta({
      ...makeDelta([]),
      removed_node_ids: [SOLANA_NODE.node_id],
      updated_nodes: [],
      added_nodes: [],
      added_edges: [],
    });

    expect(useGraphStore.getState().nodeAssetScopes.has(SOLANA_NODE.node_id)).toBe(false);
  });

  it('importSnapshot ignores Bitcoin, non-address, and unknown nodeAssetScopes entries', () => {
    const snapshot = JSON.stringify({
      sessionId: 'sess-scope',
      nodes: [ROOT, SOLANA_NODE, BITCOIN_NODE, ENTITY_NODE],
      edges: [],
      positions: {},
      branches: [],
      nodeAssetScopes: {
        [ROOT.node_id]: [ETH_USDC_SELECTOR],
        [SOLANA_NODE.node_id]: [],
        [BITCOIN_NODE.node_id]: [{
          mode: 'native',
          chain: 'bitcoin',
          asset_symbol: 'BTC',
        }],
        [ENTITY_NODE.node_id]: [ETH_USDC_SELECTOR],
        'missing:address:0xdead': [ETH_USDC_SELECTOR],
      },
    });

    const restored = useGraphStore.getState().importSnapshot(snapshot);

    expect(restored).toBe(true);
    expect(useGraphStore.getState().nodeAssetScopes).toEqual(
      new Map([
        [ROOT.node_id, [ETH_USDC_SELECTOR]],
        [SOLANA_NODE.node_id, []],
      ]),
    );
  });
});
