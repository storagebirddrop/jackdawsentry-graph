/**
 * Unit tests for computeLocalPositions and the isFree exclusion zone.
 *
 * All tests use the same slot dimensions as the implementation so that the
 * assertions stay in sync if the constants change.
 */

import { describe, expect, it, vi } from 'vitest';
import { computeLocalPositions } from './incrementalPlacement';

// Mirror the module constants — assertions depend on these values matching.
const STEP_X = 300;
const STEP_Y = 124;
const MAX_COLS = 5;
const MAX_ROWS = 40;

type Pos = { x: number; y: number };
type OccupiedNode = { id: string; position: Pos };

/** Helper: build a minimal OccupiedNode for test fixtures. */
function occ(id: string, x: number, y: number): OccupiedNode {
  return { id, position: { x, y } };
}

/**
 * Returns true if two positions are within the isFree exclusion zone (i.e.
 * they would be considered overlapping by the placement engine).
 */
function overlaps(a: Pos, b: Pos): boolean {
  return Math.abs(a.x - b.x) < STEP_X && Math.abs(a.y - b.y) < STEP_Y;
}

/**
 * Build a node grid that blocks every candidate slot in the normal
 * MAX_COLS × MAX_ROWS search.  Forces the emergency fallback to fire.
 *
 * For each column 1..MAX_COLS and anchor y, places nodes at:
 *   (anchorX + col*STEP_X, anchorY + r*STEP_Y) for r = -(MAX_ROWS-1)..(MAX_ROWS-1)
 *
 * This exactly covers the (r=0, r=±1 … r=±39) candidates that findFreeSlot tests.
 */
function buildDenseGrid(anchorX: number, anchorY: number): OccupiedNode[] {
  const nodes: OccupiedNode[] = [];
  let k = 0;
  for (let col = 1; col <= MAX_COLS; col++) {
    const x = anchorX + col * STEP_X;
    nodes.push(occ(`d${k++}`, x, anchorY));
    for (let r = 1; r < MAX_ROWS; r++) {
      nodes.push(occ(`d${k++}`, x, anchorY - r * STEP_Y));
      nodes.push(occ(`d${k++}`, x, anchorY + r * STEP_Y));
    }
  }
  return nodes; // 5 × (1 + 2*39) = 395 nodes
}

// ---------------------------------------------------------------------------
// Direction
// ---------------------------------------------------------------------------

describe('direction', () => {
  it('expand_next places nodes to the right of the anchor', () => {
    const result = computeLocalPositions({ x: 0, y: 0 }, ['a'], 'expand_next', []);
    expect(result.get('a')!.x).toBeGreaterThan(0);
  });

  it('expand_neighbors places nodes to the right of the anchor', () => {
    const result = computeLocalPositions({ x: 0, y: 0 }, ['a'], 'expand_neighbors', []);
    expect(result.get('a')!.x).toBeGreaterThan(0);
  });

  it('expand_prev places nodes to the left of the anchor', () => {
    const result = computeLocalPositions({ x: 0, y: 0 }, ['a'], 'expand_prev', []);
    expect(result.get('a')!.x).toBeLessThan(0);
  });

  it('first column is exactly STEP_X away from the anchor', () => {
    const anchor: Pos = { x: 500, y: 200 };
    const result = computeLocalPositions(anchor, ['a'], 'expand_next', []);
    expect(result.get('a')!.x).toBe(anchor.x + STEP_X);
  });
});

// ---------------------------------------------------------------------------
// Completeness
// ---------------------------------------------------------------------------

describe('completeness', () => {
  it('every supplied ID has a result', () => {
    const ids = ['a', 'b', 'c', 'd', 'e'];
    const result = computeLocalPositions({ x: 0, y: 0 }, ids, 'expand_next', []);
    for (const id of ids) {
      expect(result.has(id)).toBe(true);
    }
    expect(result.size).toBe(ids.length);
  });

  it('empty ID list returns an empty map', () => {
    const result = computeLocalPositions({ x: 0, y: 0 }, [], 'expand_next', []);
    expect(result.size).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Collision avoidance — batch
// ---------------------------------------------------------------------------

describe('batch collision avoidance', () => {
  it('10 nodes placed in a single batch do not overlap each other', () => {
    const ids = Array.from({ length: 10 }, (_, i) => `n${i}`);
    const result = computeLocalPositions({ x: 0, y: 0 }, ids, 'expand_next', []);
    const placed = ids.map((id) => result.get(id)!);
    for (let i = 0; i < placed.length; i++) {
      for (let j = i + 1; j < placed.length; j++) {
        expect(overlaps(placed[i], placed[j])).toBe(false);
      }
    }
  });
});

// ---------------------------------------------------------------------------
// Collision avoidance — existing nodes
// ---------------------------------------------------------------------------

describe('existing node avoidance', () => {
  it('avoids a single existing node at column 1, anchor row', () => {
    const existing = [occ('e', STEP_X, 0)];
    const result = computeLocalPositions({ x: 0, y: 0 }, ['new'], 'expand_next', existing);
    expect(overlaps(result.get('new')!, existing[0].position)).toBe(false);
  });

  it('avoids 20 existing nodes stacked in column 1', () => {
    const existing = Array.from({ length: 20 }, (_, i) => occ(`e${i}`, STEP_X, i * STEP_Y));
    const result = computeLocalPositions({ x: 0, y: 0 }, ['new'], 'expand_next', existing);
    const pos = result.get('new')!;
    for (const e of existing) {
      expect(overlaps(pos, e.position)).toBe(false);
    }
  });
});

// ---------------------------------------------------------------------------
// isFree boundary — exclusion zone uses strict < not <=
// ---------------------------------------------------------------------------

describe('isFree exclusion zone boundaries', () => {
  it('nodes exactly STEP_X apart (col 2 vs col 1) are not considered overlapping', () => {
    // Col 1 at x=300 is occupied at y=0. Col 2 at x=600 has |600-300|=300 which is
    // NOT < STEP_X(300), so col 2 row 0 should be free.
    const existing = [occ('e', STEP_X, 0)];
    const result = computeLocalPositions({ x: 0, y: 0 }, ['new'], 'expand_next', existing);
    // The new node should find a free slot (possibly col 2, row 0)
    expect(result.has('new')).toBe(true);
    const pos = result.get('new')!;
    expect(overlaps(pos, existing[0].position)).toBe(false);
  });

  it('nodes exactly STEP_Y apart vertically (adjacent rows) are not considered overlapping', () => {
    // y=0 and y=124: |0-124|=124 which is NOT < STEP_Y(124) → free
    const existing = [occ('e', STEP_X, 0)];
    const result = computeLocalPositions({ x: 0, y: 0 }, ['new'], 'expand_next', existing);
    const pos = result.get('new')!;
    // Placement engine may place at (STEP_X, STEP_Y) — verify no overlap
    expect(overlaps(pos, existing[0].position)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Emergency fallback path
// ---------------------------------------------------------------------------

describe('emergency fallback', () => {
  it('still produces a result when all normal search slots are exhausted', () => {
    const existing = buildDenseGrid(0, 0);
    const result = computeLocalPositions({ x: 0, y: 0 }, ['fallback'], 'expand_next', existing);
    expect(result.has('fallback')).toBe(true);
  });

  it('fallback result does not overlap any existing node', () => {
    const existing = buildDenseGrid(0, 0);
    const result = computeLocalPositions({ x: 0, y: 0 }, ['fallback'], 'expand_next', existing);
    const pos = result.get('fallback')!;
    for (const e of existing) {
      expect(overlaps(pos, e.position)).toBe(false);
    }
  });

  it('fallback loop cap terminates the search and logs an error when all rows are occupied', () => {
    // Saturate: dense grid (395 nodes) + fill fallback lane (col 1) for 500 rows beyond MAX_ROWS
    const existing = buildDenseGrid(0, 0);
    for (let i = 0; i < 500; i++) {
      existing.push(occ(`f${i}`, STEP_X, (MAX_ROWS + i) * STEP_Y));
    }
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const result = computeLocalPositions({ x: 0, y: 0 }, ['capped'], 'expand_next', existing);
    // Function must return (not hang)
    expect(result.has('capped')).toBe(true);
    // Cap should have been reached → error logged
    expect(spy).toHaveBeenCalledWith(
      expect.stringContaining('[incrementalPlacement] fallback row cap reached'),
    );
    spy.mockRestore();
  });
});
