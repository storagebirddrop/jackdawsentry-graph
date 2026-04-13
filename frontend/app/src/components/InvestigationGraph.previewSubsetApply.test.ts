// @vitest-environment jsdom
/**
 * Integration tests for the Filter & Preview panel and candidate subset apply flow.
 *
 * Covered contracts:
 * - "Preview next/prev/around" fires expandNode with max_results + time filters
 *   but does NOT commit the delta to the canvas store.
 * - "Apply all" commits the full preview delta (all edges + nodes).
 * - "Apply selected (N)" commits only the checked edges and their reachable nodes.
 * - "Dismiss" clears the preview without touching the canvas.
 * - Selecting a different node discards any stale preview.
 * - Date-range inputs are forwarded as time_from / time_to on the expand request.
 */

import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import type {
  AssetSelector,
  ExpandRequest,
  ExpansionResponseV2,
  InvestigationEdge,
  InvestigationNode,
} from '../types/graph';
import { useGraphStore } from '../store/graphStore';
import InvestigationGraph from './InvestigationGraph';

// ─── Hoisted mocks ────────────────────────────────────────────────────────────

const {
  expandNodeMock,
  getAssetOptionsMock,
  saveWorkspaceMock,
} = vi.hoisted(() => ({
  expandNodeMock: vi.fn(),
  getAssetOptionsMock: vi.fn(),
  saveWorkspaceMock: vi.fn(),
}));

vi.mock('../api/client', () => ({
  expandNode: expandNodeMock,
  getAssetOptions: getAssetOptionsMock,
}));

vi.mock('../workspacePersistence', () => ({
  saveWorkspace: saveWorkspaceMock,
}));

vi.mock('../layout/elkLayout', () => ({
  computeElkLayout: vi.fn(async () => new Map()),
}));

vi.mock('../layout/incrementalPlacement', () => ({
  buildLocalLayoutNeighborhood: vi.fn(() => ({ nodes: [], edges: [], fixedPositions: new Map() })),
  collectMeasuredNodeSizes: vi.fn(() => new Map()),
  createLocalNodePlacements: vi.fn(() => new Map()),
  isEligibleForElkRefinement: vi.fn(() => false),
  resolveNodeCollisions: vi.fn(
    ({ initialPositions }: { initialPositions: Map<string, { x: number; y: number }> }) =>
      initialPositions,
  ),
}));

vi.mock('./GraphAppearancePanel', () => ({ default: () => null }));
vi.mock('./IngestPoller', () => ({ default: () => null }));
vi.mock('./FilterPanel', async () => {
  const actual = await vi.importActual<typeof import('./FilterPanel')>('./FilterPanel');
  return { ...actual, default: () => null };
});

vi.mock('@xyflow/react', async () => {
  const react = await import('react');

  const ReactFlow = ({
    nodes = [],
    edges = [],
    onNodeClick,
    onEdgeClick,
    onPaneClick,
    onInit,
    children,
  }: Record<string, unknown>) => {
    react.useEffect(() => {
      if (typeof onInit === 'function') {
        (onInit as (inst: { fitView: () => void; getNodes: () => unknown[] }) => void)({
          fitView: () => {},
          getNodes: () => nodes as unknown[],
        });
      }
    }, [nodes, onInit]);

    return react.createElement(
      'div',
      { 'data-testid': 'react-flow' },
      ...(nodes as Array<{ id: string }>).map((node) =>
        react.createElement(
          'button',
          {
            key: `node-${node.id}`,
            type: 'button',
            onClick: (e: MouseEvent) => {
              e.stopPropagation();
              (onNodeClick as ((e: MouseEvent, n: { id: string }) => void) | undefined)?.(e, node);
            },
          },
          `Select node ${node.id}`,
        ),
      ),
      ...(edges as Array<{ id: string }>).map((edge) =>
        react.createElement(
          'button',
          {
            key: `edge-${edge.id}`,
            type: 'button',
            onClick: (e: MouseEvent) => {
              e.stopPropagation();
              (onEdgeClick as ((e: MouseEvent, edge: { id: string }) => void) | undefined)?.(
                e,
                edge,
              );
            },
          },
          `Select edge ${edge.id}`,
        ),
      ),
      react.createElement(
        'button',
        {
          key: 'pane',
          type: 'button',
          onClick: () =>
            (onPaneClick as (() => void) | undefined)?.(),
        },
        'Deselect all',
      ),
      children as React.ReactNode,
    );
  };

  return {
    ReactFlow,
    Background: () => null,
    Controls: () => null,
    MiniMap: () => null,
    BackgroundVariant: { Dots: 'dots' },
    MarkerType: { ArrowClosed: 'arrowclosed' },
    useNodesState: (initial: unknown[]) => {
      const [nodes, setNodes] = react.useState(initial);
      return [nodes, setNodes, vi.fn()] as const;
    },
    useEdgesState: (initial: unknown[]) => {
      const [edges, setEdges] = react.useState(initial);
      return [edges, setEdges, vi.fn()] as const;
    },
  };
});

// ─── Test fixtures ────────────────────────────────────────────────────────────

const SESSION_ID = 'sess-preview-subset-test';

const SEED_NODE: InvestigationNode = {
  node_id: 'ethereum:address:0xseed',
  node_type: 'address',
  chain: 'ethereum',
  branch_id: 'branch-1',
  path_id: 'path-1',
  lineage_id: 'lineage-seed',
  depth: 0,
  is_seed: true,
  expandable_directions: ['prev', 'next', 'neighbors'],
  address_data: { address: '0xseed', chain: 'ethereum' },
};

const OTHER_NODE: InvestigationNode = {
  node_id: 'ethereum:address:0xother',
  node_type: 'address',
  chain: 'ethereum',
  branch_id: 'branch-1',
  path_id: 'path-1',
  lineage_id: 'lineage-other',
  depth: 1,
  expandable_directions: ['prev', 'next', 'neighbors'],
  address_data: { address: '0xother', chain: 'ethereum' },
};

// Candidate nodes returned by the preview response (not yet on canvas).
const PREVIEW_NODE_A: InvestigationNode = {
  node_id: 'ethereum:address:0xnew1',
  node_type: 'address',
  chain: 'ethereum',
  branch_id: 'branch-1',
  path_id: 'path-1',
  lineage_id: 'lineage-new1',
  depth: 2,
  expandable_directions: ['next'],
  address_data: { address: '0xnew1', chain: 'ethereum' },
};

const PREVIEW_NODE_B: InvestigationNode = {
  node_id: 'ethereum:address:0xnew2',
  node_type: 'address',
  chain: 'ethereum',
  branch_id: 'branch-1',
  path_id: 'path-1',
  lineage_id: 'lineage-new2',
  depth: 2,
  expandable_directions: ['next'],
  address_data: { address: '0xnew2', chain: 'ethereum' },
};

// Candidate edges returned by the preview response.
const PREVIEW_EDGE_A: InvestigationEdge = {
  edge_id: 'edge-preview-a',
  edge_type: 'transfer',
  source_node_id: SEED_NODE.node_id,
  target_node_id: PREVIEW_NODE_A.node_id,
  direction: 'forward',
  branch_id: 'branch-1',
  tx_hash: '0xtxa',
  tx_chain: 'ethereum',
  asset_symbol: 'USDC',
  chain_asset_id: '0xa0b8',
};

const PREVIEW_EDGE_B: InvestigationEdge = {
  edge_id: 'edge-preview-b',
  edge_type: 'transfer',
  source_node_id: SEED_NODE.node_id,
  target_node_id: PREVIEW_NODE_B.node_id,
  direction: 'forward',
  branch_id: 'branch-1',
  tx_hash: '0xtxb',
  tx_chain: 'ethereum',
  asset_symbol: 'ETH',
};

const ETH_USDC_SELECTOR: AssetSelector = {
  mode: 'asset',
  chain: 'ethereum',
  chain_asset_id: '0xa0b8',
  asset_symbol: 'USDC',
  canonical_asset_id: 'usdc',
};

const ETH_NATIVE_SELECTOR: AssetSelector = {
  mode: 'native',
  chain: 'ethereum',
  asset_symbol: 'ETH',
};

function makePreviewResponse(): ExpansionResponseV2 {
  return {
    session_id: SESSION_ID,
    branch_id: 'branch-1',
    operation_id: 'op-preview-next',
    operation_type: 'expand_next',
    seed_node_id: SEED_NODE.node_id,
    seed_lineage_id: SEED_NODE.lineage_id,
    added_nodes: [PREVIEW_NODE_A, PREVIEW_NODE_B],
    added_edges: [PREVIEW_EDGE_A, PREVIEW_EDGE_B],
    layout_hints: { suggested_layout: 'layered' },
    chain_context: { primary_chain: 'ethereum', chains_present: ['ethereum'] },
    asset_context: { assets_present: ['USDC', 'ETH'], canonical_asset_ids: ['usdc'] },
  } as ExpansionResponseV2;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function seedStore(): void {
  const store = useGraphStore.getState();
  store.reset();
  store.initSession(SESSION_ID, SEED_NODE);
  store.applyExpansionDelta({
    session_id: SESSION_ID,
    branch_id: 'branch-1',
    operation_id: 'seed-delta',
    operation_type: 'expand_neighbors',
    seed_node_id: SEED_NODE.node_id,
    seed_lineage_id: SEED_NODE.lineage_id,
    added_nodes: [OTHER_NODE],
    added_edges: [],
    layout_hints: { suggested_layout: 'layered' },
    chain_context: { primary_chain: 'ethereum', chains_present: ['ethereum'] },
  } as ExpansionResponseV2);
}

function findButton(text: string): HTMLButtonElement | null {
  return (
    (Array.from(document.querySelectorAll('button')).find(
      (b) => b.textContent?.trim() === text,
    ) as HTMLButtonElement | undefined) ?? null
  );
}

function getButton(text: string): HTMLButtonElement {
  const btn = findButton(text);
  if (!btn) {
    throw new Error(
      `Button "${text}" not found. Found: [${
        Array.from(document.querySelectorAll('button'))
          .map((b) => `"${b.textContent?.trim()}"`)
          .join(', ')
      }]`,
    );
  }
  return btn;
}

async function clickButton(text: string): Promise<void> {
  const btn = getButton(text);
  await act(async () => {
    btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await Promise.resolve();
  });
}

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function waitFor<T>(assertion: () => T): Promise<T> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      return assertion();
    } catch (error) {
      lastError = error;
      await flush();
    }
  }
  throw lastError;
}

function queryLabeledInput(text: string, inputType: 'radio' | 'checkbox'): HTMLInputElement | null {
  const label = Array.from(document.querySelectorAll('label')).find(
    (candidate) => candidate.textContent?.trim() === text,
  );
  return (label?.querySelector(`input[type="${inputType}"]`) as HTMLInputElement | null) ?? null;
}

function getLabeledInput(text: string, inputType: 'radio' | 'checkbox'): HTMLInputElement {
  const input = queryLabeledInput(text, inputType);
  if (!input) {
    throw new Error(`Unable to find ${inputType} labeled "${text}"`);
  }
  return input;
}

async function clickInput(input: HTMLInputElement): Promise<void> {
  await act(async () => {
    input.click();
    await Promise.resolve();
  });
}

async function enableSpecificAssetScope(labels: string[]): Promise<void> {
  await clickInput(getLabeledInput('Specific assets', 'radio'));
  for (const label of labels) {
    await clickInput(getLabeledInput(label, 'checkbox'));
  }
  await flush();
}

function getPreviewCandidateCheckbox(counterpartyText: string): HTMLInputElement {
  const label = Array.from(document.querySelectorAll('label')).find(
    (candidate) => candidate.textContent?.includes(counterpartyText),
  );
  const checkbox = label?.querySelector('input[type="checkbox"]') as HTMLInputElement | null;
  if (!checkbox) {
    throw new Error(`Unable to find preview candidate checkbox for "${counterpartyText}"`);
  }
  return checkbox;
}

async function setDateInputValue(input: HTMLInputElement, value: string): Promise<void> {
  const nativeSetter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    'value',
  )?.set;
  await act(async () => {
    nativeSetter?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    await Promise.resolve();
  });
}

/**
 * Advance from a clean render to the preview-loaded state:
 * seed node selected → filter panel open → "Preview next" clicked → preview result rendered.
 */
async function reachPreviewState(): Promise<void> {
  expandNodeMock.mockResolvedValueOnce(makePreviewResponse());
  await clickButton(`Select node ${SEED_NODE.node_id}`);
  await flush();
  await clickButton('Filter & Preview ▼');
  await flush();
  await clickButton('Preview next');
  await flush();
}

// ─── Test suite ───────────────────────────────────────────────────────────────

describe('InvestigationGraph preview and subset apply', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(async () => {
    expandNodeMock.mockReset();
    getAssetOptionsMock.mockReset();
    saveWorkspaceMock.mockReset();

    getAssetOptionsMock.mockResolvedValue({
      session_id: SESSION_ID,
      seed_node_id: SEED_NODE.node_id,
      seed_lineage_id: SEED_NODE.lineage_id,
      options: [
        { mode: 'all', chain: 'ethereum', display_label: 'All assets' },
        { ...ETH_USDC_SELECTOR, display_label: 'USDC' },
        { ...ETH_NATIVE_SELECTOR, display_label: 'ETH' },
      ],
    });
    saveWorkspaceMock.mockResolvedValue(undefined);

    seedStore();

    container = document.createElement('div');
    document.body.innerHTML = '';
    document.body.appendChild(container);
    root = createRoot(container);

    await act(async () => {
      root.render(
        React.createElement(InvestigationGraph, {
          sessionId: SESSION_ID,
          onStartNewInvestigation: vi.fn(),
        }),
      );
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    document.body.innerHTML = '';
    useGraphStore.getState().reset();
  });

  it('preview fires expandNode with max_results and does not commit delta to canvas', async () => {
    expandNodeMock.mockResolvedValueOnce(makePreviewResponse());

    await clickButton(`Select node ${SEED_NODE.node_id}`);
    await flush();
    await clickButton('Filter & Preview ▼');
    await flush();
    await clickButton('Preview next');
    await flush();

    // expandNode called exactly once with preview-specific options
    expect(expandNodeMock).toHaveBeenCalledTimes(1);
    const [calledSessionId, calledRequest] = expandNodeMock.mock.calls[0] as [
      string,
      ExpandRequest,
    ];
    expect(calledSessionId).toBe(SESSION_ID);
    expect(calledRequest.operation_type).toBe('expand_next');
    expect(calledRequest.seed_node_id).toBe(SEED_NODE.node_id);
    expect(calledRequest.options).toMatchObject({ max_results: 25 });

    // Canvas store unchanged — preview edges and nodes not committed
    const { nodeMap, edgeMap } = useGraphStore.getState();
    expect(edgeMap.size).toBe(0);
    expect(nodeMap.has(PREVIEW_NODE_A.node_id)).toBe(false);
    expect(nodeMap.has(PREVIEW_NODE_B.node_id)).toBe(false);

    // Preview panel shows Apply / Dismiss controls
    expect(findButton('Apply all')).not.toBeNull();
    expect(findButton('Dismiss')).not.toBeNull();
    // Both candidate edges are pre-selected (initial state)
    expect(findButton('Apply selected (2)')).not.toBeNull();
  });

  it('"Apply all" commits the complete preview delta to the canvas', async () => {
    await reachPreviewState();
    const beforeNodeCount = useGraphStore.getState().nodeMap.size;

    await clickButton('Apply all');
    await flush();

    const { nodeMap, edgeMap } = useGraphStore.getState();

    // Both preview edges committed
    expect(edgeMap.has(PREVIEW_EDGE_A.edge_id)).toBe(true);
    expect(edgeMap.has(PREVIEW_EDGE_B.edge_id)).toBe(true);
    expect(edgeMap.size).toBe(2);

    // Both preview nodes added on top of the original canvas
    expect(nodeMap.has(PREVIEW_NODE_A.node_id)).toBe(true);
    expect(nodeMap.has(PREVIEW_NODE_B.node_id)).toBe(true);
    expect(nodeMap.size).toBe(beforeNodeCount + 2);

    // Preview panel dismissed after applying
    expect(findButton('Apply all')).toBeNull();
    expect(findButton('Dismiss')).toBeNull();
  });

  it('"Apply selected" commits only the checked edges and their reachable nodes', async () => {
    await reachPreviewState();

    // "None" deselects all candidate edges.
    await clickButton('None');
    await flush();

    // All edges cleared — now re-select only edge A by clicking its checkbox
    // (toggling from unchecked → checked; jsdom toggles on click).
    const checkboxes = Array.from(
      document.querySelectorAll('input[type="checkbox"]'),
    ) as HTMLInputElement[];
    expect(checkboxes).toHaveLength(2);
    expect(checkboxes[0].checked).toBe(false);

    await act(async () => {
      // Clicking an unchecked checkbox toggles it to checked in jsdom.
      checkboxes[0].dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
    });
    await flush();

    // Counter should now show 1 selected
    expect(findButton('Apply selected (1)')).not.toBeNull();

    await clickButton('Apply selected (1)');
    await flush();

    const { nodeMap, edgeMap } = useGraphStore.getState();

    // Only the checked edge (A) and its target node committed
    expect(edgeMap.has(PREVIEW_EDGE_A.edge_id)).toBe(true);
    expect(nodeMap.has(PREVIEW_NODE_A.node_id)).toBe(true);

    // Unchecked edge (B) and its target node excluded
    expect(edgeMap.has(PREVIEW_EDGE_B.edge_id)).toBe(false);
    expect(nodeMap.has(PREVIEW_NODE_B.node_id)).toBe(false);

    expect(edgeMap.size).toBe(1);
  });

  it('multi-asset preview applies only the selected candidate subset', async () => {
    expandNodeMock.mockResolvedValueOnce(makePreviewResponse());

    await clickButton(`Select node ${SEED_NODE.node_id}`);
    await flush();

    await waitFor(() => {
      expect(getLabeledInput('All assets', 'radio').checked).toBe(true);
    });

    await enableSpecificAssetScope(['USDC', 'ETH']);

    await clickButton('Filter & Preview ▼');
    await flush();
    await clickButton('Preview next');
    await flush();

    expect(expandNodeMock).toHaveBeenCalledTimes(1);
    const [, request] = expandNodeMock.mock.calls[0] as [string, ExpandRequest];
    expect(request.options).toEqual(
      expect.objectContaining({
        asset_selectors: [ETH_USDC_SELECTOR, ETH_NATIVE_SELECTOR],
        max_results: 25,
      }),
    );

    await clickButton('None');
    await flush();
    expect(findButton('Apply selected (0)')?.disabled).toBe(true);

    await clickInput(getPreviewCandidateCheckbox('0xnew1'));
    await flush();

    expect(findButton('Apply selected (1)')).not.toBeNull();
    await clickButton('Apply selected (1)');
    await flush();

    const { nodeMap, edgeMap } = useGraphStore.getState();
    expect(edgeMap.has(PREVIEW_EDGE_A.edge_id)).toBe(true);
    expect(edgeMap.has(PREVIEW_EDGE_B.edge_id)).toBe(false);
    expect(nodeMap.has(PREVIEW_NODE_A.node_id)).toBe(true);
    expect(nodeMap.has(PREVIEW_NODE_B.node_id)).toBe(false);
  });

  it('"Dismiss" clears the preview without modifying the canvas', async () => {
    await reachPreviewState();

    const { nodeMap: beforeNodeMap, edgeMap: beforeEdgeMap } = useGraphStore.getState();
    const beforeNodeCount = beforeNodeMap.size;
    const beforeEdgeCount = beforeEdgeMap.size;

    await clickButton('Dismiss');
    await flush();

    const { nodeMap, edgeMap } = useGraphStore.getState();
    expect(nodeMap.size).toBe(beforeNodeCount);
    expect(edgeMap.size).toBe(beforeEdgeCount);

    // Preview panel gone
    expect(findButton('Apply all')).toBeNull();
    expect(findButton('Dismiss')).toBeNull();
    expect(findButton('Apply selected (2)')).toBeNull();
  });

  it('selecting a different node discards stale preview without touching the canvas', async () => {
    await reachPreviewState();

    // Preview panel is visible
    expect(findButton('Apply all')).not.toBeNull();

    // Select the other node
    await clickButton(`Select node ${OTHER_NODE.node_id}`);
    await flush();

    // Preview should have been discarded
    expect(findButton('Apply all')).toBeNull();
    expect(findButton('Dismiss')).toBeNull();

    // Canvas unchanged
    expect(useGraphStore.getState().edgeMap.size).toBe(0);
  });

  it('date-range inputs are forwarded as time_from and time_to on the expand request', async () => {
    expandNodeMock.mockResolvedValueOnce(makePreviewResponse());

    await clickButton(`Select node ${SEED_NODE.node_id}`);
    await flush();
    await clickButton('Filter & Preview ▼');
    await flush();

    // Fill in both date inputs
    const dateInputs = Array.from(
      document.querySelectorAll('input[type="date"]'),
    ) as HTMLInputElement[];
    expect(dateInputs.length).toBeGreaterThanOrEqual(2);
    const [fromInput, toInput] = dateInputs;

    // Use the native HTMLInputElement value setter so React's synthetic event
    // system reads the updated value correctly from ev.target.value.
    const nativeSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    )?.set;
    await act(async () => {
      nativeSetter?.call(fromInput, '2024-01-01');
      fromInput.dispatchEvent(new Event('input', { bubbles: true }));
      fromInput.dispatchEvent(new Event('change', { bubbles: true }));
      nativeSetter?.call(toInput, '2024-06-30');
      toInput.dispatchEvent(new Event('input', { bubbles: true }));
      toInput.dispatchEvent(new Event('change', { bubbles: true }));
      await Promise.resolve();
    });

    await clickButton('Preview next');
    await flush();

    expect(expandNodeMock).toHaveBeenCalledTimes(1);
    const [, request] = expandNodeMock.mock.calls[0] as [string, ExpandRequest];
    expect(request.options).toMatchObject({
      time_from: '2024-01-01',
      time_to: '2024-06-30',
      max_results: 25,
    });
  });

  it('combines date-filtered preview requests with the active multi-asset scope', async () => {
    expandNodeMock.mockResolvedValueOnce(makePreviewResponse());

    await clickButton(`Select node ${SEED_NODE.node_id}`);
    await flush();

    await waitFor(() => {
      expect(getLabeledInput('All assets', 'radio').checked).toBe(true);
    });

    await enableSpecificAssetScope(['USDC', 'ETH']);
    await clickButton('Filter & Preview ▼');
    await flush();

    const dateInputs = Array.from(
      document.querySelectorAll('input[type="date"]'),
    ) as HTMLInputElement[];
    expect(dateInputs.length).toBeGreaterThanOrEqual(2);
    const [fromInput, toInput] = dateInputs;

    await setDateInputValue(fromInput, '2024-02-01');
    await setDateInputValue(toInput, '2024-07-15');

    await clickButton('Preview next');
    await flush();

    expect(expandNodeMock).toHaveBeenCalledTimes(1);
    const [, request] = expandNodeMock.mock.calls[0] as [string, ExpandRequest];
    expect(request.options).toEqual(
      expect.objectContaining({
        asset_selectors: [ETH_USDC_SELECTOR, ETH_NATIVE_SELECTOR],
        time_from: '2024-02-01',
        time_to: '2024-07-15',
        max_results: 25,
      }),
    );
  });

  it('"Apply selected" button is disabled when no edges are checked', async () => {
    await reachPreviewState();

    // "None" bulk-deselects all candidate edges in one click.
    await clickButton('None');
    await flush();

    // "Apply selected (0)" button exists but is disabled
    const applySelectedBtn = findButton('Apply selected (0)');
    expect(applySelectedBtn).not.toBeNull();
    expect(applySelectedBtn!.disabled).toBe(true);

    // Clicking the disabled button must not change canvas state
    await act(async () => {
      applySelectedBtn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
    });
    await flush();

    expect(useGraphStore.getState().edgeMap.size).toBe(0);
    // Preview panel still visible (not dismissed by clicking a disabled button)
    expect(findButton('Dismiss')).not.toBeNull();
  });
});
