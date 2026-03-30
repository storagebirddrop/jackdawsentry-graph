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
import type { InvestigationNode, ExpansionResponseV2, BridgeHopStatusResponse } from '../types/graph';

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
