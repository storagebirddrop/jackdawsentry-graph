/**
 * Deterministic, collision-aware local placement for incremental graph expansion.
 *
 * Positions new nodes in directional lanes around an anchor node without
 * overlapping any already-rendered node.  This runs synchronously in the
 * event handler immediately after the store delta is applied, so the browser
 * never renders the {0,0} default position.
 *
 * Slot dimensions deliberately mirror ELK's configured spacing values so that
 * if ELK does run as a fallback the positions it computes are close to the
 * pre-placed ones, minimising visual movement.
 *
 * ELK spacing references (see elkLayout.ts):
 *   elk.layered.spacing.nodeNodeBetweenLayers = 120  → used as GAP_X
 *   elk.spacing.nodeNode                       =  60  → used as GAP_Y
 *   NODE_WIDTH  = 180
 *   NODE_HEIGHT =  64
 */

/** Horizontal step between successive lane columns (layer width + layer gap). */
const STEP_X = 300; // 180 + 120

/** Vertical step between successive row slots (node height + node gap). */
const STEP_Y = 124; // 64 + 60

/** Maximum number of columns to try before falling back. */
const MAX_COLS = 5;

/** Maximum number of row slots to search per column before giving up. */
const MAX_ROWS = 40;

/** Hard iteration cap for the emergency fallback row search. */
const MAX_FALLBACK_ROWS = 500;

type Position = { x: number; y: number };

interface OccupiedNode {
  id: string;
  position: Position;
}

/**
 * Returns true when placing a node at (cx, cy) would be within one slot of
 * an existing node at (nx, ny).  The exclusion zone is one full STEP_X ×
 * STEP_Y rectangle — this matches ELK's layer/node spacing and ensures the
 * graph remains readable without needing a separate spacing pass.
 */
function isFree(cx: number, cy: number, occupied: readonly OccupiedNode[]): boolean {
  for (const node of occupied) {
    if (
      Math.abs(cx - node.position.x) < STEP_X &&
      Math.abs(cy - node.position.y) < STEP_Y
    ) {
      return false;
    }
  }
  return true;
}

/**
 * Search outward from anchorY in the given column for the first free row slot.
 * Search pattern: 0, −1, +1, −2, +2, … to keep new nodes vertically centred
 * on the anchor rather than piling downward.
 *
 * Returns the free position or null if MAX_ROWS is exhausted.
 */
function findFreeSlot(
  x: number,
  anchorY: number,
  occupied: readonly OccupiedNode[],
): Position | null {
  for (let r = 0; r < MAX_ROWS; r++) {
    const candidates = r === 0 ? [anchorY] : [anchorY - r * STEP_Y, anchorY + r * STEP_Y];
    for (const y of candidates) {
      if (isFree(x, y, occupied)) return { x, y };
    }
  }
  return null;
}

/**
 * Compute deterministic local positions for a batch of newly added nodes.
 *
 * Nodes are placed in directional lanes starting one STEP_X away from the
 * anchor.  Each placed node is immediately added to the occupied set so later
 * nodes in the same batch avoid it.
 *
 * @param anchorPos     Current canvas position of the node that was expanded.
 * @param newNodeIds    IDs of nodes to place, in stable order.
 * @param direction     Expansion direction: 'expand_next' and 'expand_neighbors'
 *                      place rightward; 'expand_prev' places leftward.
 * @param existingNodes All currently rendered nodes (including the anchor).
 * @returns             Map of nodeId → {x, y}.  Every supplied ID has an entry.
 */
export function computeLocalPositions(
  anchorPos: Position,
  newNodeIds: readonly string[],
  direction: 'expand_next' | 'expand_prev' | 'expand_neighbors',
  existingNodes: readonly OccupiedNode[],
): Map<string, Position> {
  const result = new Map<string, Position>();
  // Working copy grows as nodes are placed so each new node avoids its peers.
  const occupied: OccupiedNode[] = Array.from(existingNodes);

  const xDir = direction === 'expand_prev' ? -1 : 1;

  for (const nodeId of newNodeIds) {
    let placed = false;

    for (let col = 1; col <= MAX_COLS && !placed; col++) {
      const x = anchorPos.x + xDir * col * STEP_X;
      const slot = findFreeSlot(x, anchorPos.y, occupied);
      if (slot !== null) {
        result.set(nodeId, slot);
        occupied.push({ id: nodeId, position: slot });
        placed = true;
      }
    }

    if (!placed) {
      // Emergency fallback: exhausted all MAX_COLS × MAX_ROWS candidates.
      // Extend the Y search in the first lane until a free slot is found.
      // isFree is still called so we never place on top of an existing node.
      // MAX_FALLBACK_ROWS caps the loop to prevent an infinite hang if the
      // occupied set is pathologically dense.
      const fallbackX = anchorPos.x + xDir * STEP_X;
      let fallbackY = anchorPos.y + MAX_ROWS * STEP_Y;
      let cap = 0;
      while (!isFree(fallbackX, fallbackY, occupied) && cap < MAX_FALLBACK_ROWS) {
        fallbackY += STEP_Y;
        cap++;
      }
      if (cap >= MAX_FALLBACK_ROWS) {
        console.error('[incrementalPlacement] fallback row cap reached — graph is pathologically dense');
      }
      const fallback: Position = { x: fallbackX, y: fallbackY };
      result.set(nodeId, fallback);
      occupied.push({ id: nodeId, position: fallback });
    }
  }

  return result;
}
