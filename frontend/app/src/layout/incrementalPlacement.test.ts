/**
 * Regression test for the expand_prev ELK-refinement placement bug.
 *
 * ROOT CAUSE
 * ----------
 * createLocalNodePlacements correctly placed expand_prev nodes to the LEFT of
 * the anchor (side = -1).  However their placementSource was 'local_expansion',
 * which caused the ELK refinement useEffect in InvestigationGraph to pick them
 * up and re-run ELK on them.  ELK's interactive layered algorithm (direction:
 * RIGHT) cannot reliably place a free node to the LEFT of the only fixed node
 * in the subgraph, so it consistently pushed the new nodes to the RIGHT.
 *
 * FIX
 * ---
 * expand_prev nodes now receive placementSource: 'elk_refinement', which
 * isEligibleForElkRefinement() maps to false.  The ELK pass is therefore
 * skipped and the correct left-side positions set by incremental placement are
 * preserved.
 *
 * WHAT WOULD HAVE FAILED BEFORE THE FIX
 * --------------------------------------
 * The "ineligible for ELK refinement" assertion: before the fix,
 * expand_prev nodes had placementSource 'local_expansion', so
 * isEligibleForElkRefinement returned true — meaning they *were* candidates for
 * the ELK pass that moved them right.  That assertion would have thrown.
 * The position assertion (x < anchor.x) exercises the same incremental
 * placement code that already worked, confirming it has not regressed.
 */

import { describe, it, expect, vi } from 'vitest';
import type { Node, Edge } from '@xyflow/react';
import { createLocalNodePlacements, isEligibleForElkRefinement } from './incrementalPlacement';
import type { NodeLayoutMetadata } from '../types/graph';

// ---------------------------------------------------------------------------
// Mock elkLayout — the module's top-level code imports an elkjs Web Worker
// via Vite's `?worker` transform, which cannot resolve in the Node test
// environment.  incrementalPlacement only uses getNodeDimensions, so we
// stub that single export with a fixed fallback size.
// ---------------------------------------------------------------------------
vi.mock('./elkLayout', () => ({
  getNodeDimensions: (_node: unknown, _measured?: unknown) => ({ width: 260, height: 150 }),
}));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const ANCHOR_X = 500;
const ANCHOR_Y = 300;
const ANCHOR_ID = 'anchor';

function makeNode(id: string, x = 0, y = 0): Node {
  return {
    id,
    type: 'address',
    position: { x, y },
    data: { node_type: 'address', depth: 0 },
  };
}

function makeEdge(id: string, source: string, target: string): Edge {
  return { id, source, target } as Edge;
}

// ---------------------------------------------------------------------------
// expand_prev: left placement
// ---------------------------------------------------------------------------

describe('expand_prev — left placement', () => {
  it('places every new node to the LEFT of the anchor', () => {
    const anchorNode = makeNode(ANCHOR_ID, ANCHOR_X, ANCHOR_Y);
    const prevIds = ['prev-a', 'prev-b', 'prev-c'];
    const newNodes = prevIds.map((id) => makeNode(id));
    // Edges mirror the backend contract: source=prev_node → target=seed_node
    const edges = prevIds.map((id) => makeEdge(`e-${id}`, id, ANCHOR_ID));

    const placements = createLocalNodePlacements({
      existingNodes: [anchorNode],
      newNodes,
      edges,
      seedNodeId: ANCHOR_ID,
      operationType: 'expand_prev',
      layoutToken: 'op-test-prev',
    });

    for (const id of prevIds) {
      const pos = placements.get(id)?.position;
      expect(pos, `${id} must have a placement`).toBeDefined();
      expect(pos!.x, `${id} must be LEFT of anchor (x < ${ANCHOR_X})`).toBeLessThan(ANCHOR_X);
    }
  });
});

// ---------------------------------------------------------------------------
// expand_prev: ELK refinement exclusion
// ---------------------------------------------------------------------------

describe('expand_prev — ELK refinement exclusion', () => {
  it('marks every expand_prev node as ineligible for ELK refinement', () => {
    const anchorNode = makeNode(ANCHOR_ID, ANCHOR_X, ANCHOR_Y);
    const prevIds = ['prev-a', 'prev-b'];
    const newNodes = prevIds.map((id) => makeNode(id));

    const placements = createLocalNodePlacements({
      existingNodes: [anchorNode],
      newNodes,
      edges: [],
      seedNodeId: ANCHOR_ID,
      operationType: 'expand_prev',
      layoutToken: 'op-test-prev',
    });

    // Simulate the candidate-selection filter from InvestigationGraph's ELK
    // refinement useEffect — only 'local_expansion' nodes enter the ELK pass.
    const refinementCandidates = newNodes
      .map((n) => n.id)
      .filter((id) => {
        const meta = placements.get(id)?.layoutMeta as NodeLayoutMetadata | undefined;
        return meta ? isEligibleForElkRefinement(meta) : false;
      });

    for (const prevNodeId of prevIds) {
      expect(refinementCandidates).not.toContain(prevNodeId);
    }
  });
});

// ---------------------------------------------------------------------------
// expand_next: sanity — nodes remain ELK-refinement eligible
// ---------------------------------------------------------------------------

describe('expand_next — ELK refinement eligibility preserved', () => {
  it('marks every expand_next node as eligible for ELK refinement', () => {
    const anchorNode = makeNode(ANCHOR_ID, ANCHOR_X, ANCHOR_Y);
    const nextIds = ['next-a', 'next-b'];
    const newNodes = nextIds.map((id) => makeNode(id));

    const placements = createLocalNodePlacements({
      existingNodes: [anchorNode],
      newNodes,
      edges: nextIds.map((id) => makeEdge(`e-${id}`, ANCHOR_ID, id)),
      seedNodeId: ANCHOR_ID,
      operationType: 'expand_next',
      layoutToken: 'op-test-next',
    });

    for (const id of nextIds) {
      const meta = placements.get(id)?.layoutMeta as NodeLayoutMetadata | undefined;
      expect(meta, `${id} must have layoutMeta`).toBeDefined();
      expect(
        isEligibleForElkRefinement(meta!),
        `${id} (expand_next) must remain eligible for ELK refinement`,
      ).toBe(true);
    }
  });
});
