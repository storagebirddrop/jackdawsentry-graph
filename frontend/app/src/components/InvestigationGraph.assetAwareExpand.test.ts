// @vitest-environment jsdom

import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type {
  AssetOptionsResponse,
  AssetSelector,
  ExpandRequest,
  ExpansionResponseV2,
  InvestigationEdge,
  InvestigationNode,
} from '../types/graph';

const expandNodeMock = vi.fn();
const getAssetOptionsMock = vi.fn();
const saveWorkspaceMock = vi.fn();

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client');
  return {
    ...actual,
    expandNode: (...args: unknown[]) => expandNodeMock(...args),
    getAssetOptions: (...args: unknown[]) => getAssetOptionsMock(...args),
  };
});

vi.mock('../workspacePersistence', () => ({
  saveWorkspace: (...args: unknown[]) => saveWorkspaceMock(...args),
}));

vi.mock('../layout/elkLayout', () => ({
  computeElkLayout: vi.fn(async () => new Map()),
}));

vi.mock('../layout/incrementalPlacement', () => ({
  buildLocalLayoutNeighborhood: vi.fn(() => ({
    nodes: [],
    edges: [],
    fixedPositions: new Map(),
  })),
  collectMeasuredNodeSizes: vi.fn(() => new Map()),
  createLocalNodePlacements: vi.fn(() => new Map()),
  isEligibleForElkRefinement: vi.fn(() => false),
  resolveNodeCollisions: vi.fn(({ initialPositions }: { initialPositions: Map<string, { x: number; y: number }> }) => initialPositions),
}));

vi.mock('./GraphAppearancePanel', () => ({
  default: () => null,
}));

vi.mock('./IngestPoller', () => ({
  default: () => null,
}));

vi.mock('./FilterPanel', async () => {
  const actual = await vi.importActual<typeof import('./FilterPanel')>('./FilterPanel');
  return {
    ...actual,
    default: () => null,
  };
});

vi.mock('@xyflow/react', async () => {
  const ReactModule = await import('react');

  function useArrayState<T>(initialValue: T[]) {
    const [value, setValue] = ReactModule.useState(initialValue);
    ReactModule.useEffect(() => {
      setValue(initialValue);
    }, [initialValue]);
    return [value, setValue, () => {}] as const;
  }

  const ReactFlow = ({
    nodes = [],
    edges = [],
    onNodeClick,
    onEdgeClick,
    onPaneClick,
    onInit,
    children,
  }: Record<string, unknown>) => {
    ReactModule.useEffect(() => {
      onInit?.({
        fitView: () => {},
        getNodes: () => nodes,
      });
    }, [nodes, onInit]);

    return ReactModule.createElement(
      'div',
      { 'data-testid': 'react-flow-mock' },
      ReactModule.createElement(
        'button',
        {
          type: 'button',
          onClick: () => onPaneClick?.(),
        },
        'Select canvas',
      ),
      ...(nodes as Array<Record<string, unknown>>).flatMap((node) => {
        const data = (node.data ?? {}) as Record<string, unknown>;
        const controls = [
          ReactModule.createElement(
            'button',
            {
              key: `select-${String(node.id)}`,
              type: 'button',
              onClick: () => onNodeClick?.({}, node),
            },
            `Select node ${String(node.id)}`,
          ),
        ];

        if (typeof data.onExpandPrev === 'function') {
          controls.push(
            ReactModule.createElement(
              'button',
              {
                key: `quick-prev-${String(node.id)}`,
                type: 'button',
                onClick: () => data.onExpandPrev?.(),
              },
              `Quick prev ${String(node.id)}`,
            ),
          );
        }

        if (typeof data.onExpandNext === 'function') {
          controls.push(
            ReactModule.createElement(
              'button',
              {
                key: `quick-next-${String(node.id)}`,
                type: 'button',
                onClick: () => data.onExpandNext?.(),
              },
              `Quick next ${String(node.id)}`,
            ),
          );
        }

        return controls;
      }),
      ...(edges as Array<Record<string, unknown>>).map((edge) =>
        ReactModule.createElement(
          'button',
          {
            key: `edge-${String(edge.id)}`,
            type: 'button',
            onClick: () => onEdgeClick?.({}, edge),
          },
          `Select edge ${String(edge.id)}`,
        ),
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
    MarkerType: { ArrowClosed: 'arrow-closed' },
    useNodesState: useArrayState,
    useEdgesState: useArrayState,
  };
});

import InvestigationGraph from './InvestigationGraph';
import { useGraphStore } from '../store/graphStore';

const SESSION_ID = 'session-asset-aware-test';

const ETH_NODE: InvestigationNode = {
  node_id: 'ethereum:address:0xaaaa',
  node_type: 'address',
  chain: 'ethereum',
  address_data: {
    address: '0xaaaa',
    chain: 'ethereum',
    label: 'ETH seed',
  },
  branch_id: 'branch-1',
  path_id: 'path-1',
  lineage_id: 'lineage-eth',
  depth: 0,
  expandable_directions: ['prev', 'next', 'neighbors'],
  is_seed: true,
};

const ETH_COUNTERPARTY: InvestigationNode = {
  node_id: 'ethereum:address:0xbbbb',
  node_type: 'address',
  chain: 'ethereum',
  address_data: {
    address: '0xbbbb',
    chain: 'ethereum',
    label: 'ETH counterparty',
  },
  branch_id: 'branch-1',
  path_id: 'path-1',
  lineage_id: 'lineage-eth-counterparty',
  depth: 1,
  expandable_directions: ['prev', 'next', 'neighbors'],
};

const BTC_NODE: InvestigationNode = {
  node_id: 'bitcoin:address:bc1qexampleassetaware',
  node_type: 'address',
  chain: 'bitcoin',
  address_data: {
    address: 'bc1qexampleassetaware',
    chain: 'bitcoin',
    label: 'BTC seed',
  },
  branch_id: 'branch-1',
  path_id: 'path-2',
  lineage_id: 'lineage-btc',
  depth: 0,
  expandable_directions: ['prev', 'next', 'neighbors'],
};

const SAFE_EDGE: InvestigationEdge = {
  edge_id: 'edge-safe',
  edge_type: 'transfer',
  source_node_id: ETH_NODE.node_id,
  target_node_id: ETH_COUNTERPARTY.node_id,
  direction: 'forward',
  branch_id: 'branch-1',
  tx_hash: '0xsafe',
  tx_chain: 'ethereum',
  asset_symbol: 'USDC',
  canonical_asset_id: 'usdc',
  chain_asset_id: '0xa0b8',
};

const UNSAFE_EDGE: InvestigationEdge = {
  edge_id: 'edge-unsafe',
  edge_type: 'transfer',
  source_node_id: ETH_NODE.node_id,
  target_node_id: ETH_COUNTERPARTY.node_id,
  direction: 'forward',
  branch_id: 'branch-1',
  tx_hash: '0xunsafe',
  tx_chain: 'ethereum',
  asset_symbol: 'USDT',
  canonical_asset_id: 'tether',
};

const ETH_USDC_SELECTOR: AssetSelector = {
  mode: 'asset',
  chain: 'ethereum',
  chain_asset_id: '0xa0b8',
  asset_symbol: 'USDC',
  canonical_asset_id: 'usdc',
};

function makeEmptyExpansion(request: ExpandRequest): ExpansionResponseV2 {
  return {
    session_id: SESSION_ID,
    branch_id: 'branch-1',
    operation_id: `op-${request.operation_type}-${request.seed_node_id}`,
    operation_type: request.operation_type,
    seed_node_id: request.seed_node_id,
    seed_lineage_id: request.seed_lineage_id ?? null,
    nodes: [],
    edges: [],
    layout_hints: {
      suggested_layout: 'layered',
    },
    chain_context: {
      primary_chain: request.seed_node_id.split(':')[0] ?? 'ethereum',
      chains_present: ['ethereum', 'bitcoin'],
    },
    empty_state: {
      reason: 'no_results',
      message: 'No indexed activity in the current dataset.',
      chain: request.seed_node_id.split(':')[0] ?? 'ethereum',
      operation_type: request.operation_type,
    },
  };
}

function seedGraphStore(): void {
  const store = useGraphStore.getState();
  store.reset();
  store.initSession(SESSION_ID, ETH_NODE);
  store.applyExpansionDelta({
    session_id: SESSION_ID,
    branch_id: 'branch-1',
    operation_id: 'seed-delta',
    operation_type: 'expand_neighbors',
    seed_node_id: ETH_NODE.node_id,
    seed_lineage_id: ETH_NODE.lineage_id,
    nodes: [ETH_COUNTERPARTY, BTC_NODE],
    edges: [SAFE_EDGE, UNSAFE_EDGE],
    layout_hints: {
      suggested_layout: 'layered',
    },
    chain_context: {
      primary_chain: 'ethereum',
      chains_present: ['ethereum', 'bitcoin'],
    },
  });
}

function findButtonByText(text: string): HTMLButtonElement | null {
  return (
    Array.from(document.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === text,
    ) as HTMLButtonElement | undefined
  ) ?? null;
}

function getButtonByText(text: string): HTMLButtonElement {
  const button = findButtonByText(text);
  expect(button, `Expected button "${text}" to exist.`).not.toBeNull();
  return button as HTMLButtonElement;
}

async function clickButton(text: string): Promise<void> {
  const button = getButtonByText(text);
  await act(async () => {
    button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
}

function querySelectByLabel(labelText: string): HTMLSelectElement | null {
  const label = Array.from(document.querySelectorAll('label')).find(
    (candidate) => candidate.textContent?.includes(labelText),
  );
  return (label?.querySelector('select') as HTMLSelectElement | null) ?? null;
}

function getSelectByLabel(labelText: string): HTMLSelectElement {
  const select = querySelectByLabel(labelText);
  expect(select, `Expected select labeled "${labelText}" to exist.`).not.toBeNull();
  return select as HTMLSelectElement;
}

async function selectOption(labelText: string, optionText: string): Promise<void> {
  const select = getSelectByLabel(labelText);
  const option = Array.from(select.options).find(
    (candidate) => candidate.textContent?.trim() === optionText,
  );
  expect(option, `Expected option "${optionText}" to exist.`).not.toBeNull();

  await act(async () => {
    select.value = (option as HTMLOptionElement).value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

async function flushAsyncWork(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function nthExpandRequest(callIndex: number): ExpandRequest {
  const call = expandNodeMock.mock.calls[callIndex];
  expect(call, `Expected expandNode call #${callIndex + 1} to exist.`).toBeTruthy();
  return call[1] as ExpandRequest;
}

describe('InvestigationGraph asset-aware expand contract', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(async () => {
    expandNodeMock.mockReset();
    getAssetOptionsMock.mockReset();
    saveWorkspaceMock.mockReset();

    expandNodeMock.mockImplementation(async (_sessionId: string, request: ExpandRequest) => (
      makeEmptyExpansion(request)
    ));
    getAssetOptionsMock.mockImplementation(async (_sessionId: string, request: { seed_node_id: string; seed_lineage_id?: string }) => {
      if (request.seed_node_id !== ETH_NODE.node_id) {
        throw new Error(`Unexpected asset lookup for ${request.seed_node_id}`);
      }
      const response: AssetOptionsResponse = {
        session_id: SESSION_ID,
        seed_node_id: request.seed_node_id,
        seed_lineage_id: request.seed_lineage_id ?? null,
        options: [
          {
            mode: 'all',
            chain: 'ethereum',
            display_label: 'All assets',
          },
          {
            ...ETH_USDC_SELECTOR,
            display_label: 'USDC',
          },
        ],
      };
      return response;
    });
    saveWorkspaceMock.mockResolvedValue(undefined);

    seedGraphStore();

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

  it('sends inspector, quick-expand, edge-trace, and bitcoin requests with the active asset-aware contract', async () => {
    await clickButton(`Select node ${ETH_NODE.node_id}`);
    await flushAsyncWork();

    expect(getAssetOptionsMock).toHaveBeenCalledTimes(1);
    expect(getAssetOptionsMock).toHaveBeenNthCalledWith(1, SESSION_ID, {
      seed_node_id: ETH_NODE.node_id,
      seed_lineage_id: ETH_NODE.lineage_id,
    });

    expect(getSelectByLabel('Asset scope').options).toHaveLength(2);
    await selectOption('Asset scope', 'USDC');
    await flushAsyncWork();

    await clickButton('Expand around');
    expect(nthExpandRequest(0)).toEqual({
      seed_node_id: ETH_NODE.node_id,
      seed_lineage_id: ETH_NODE.lineage_id,
      operation_type: 'expand_neighbors',
      options: {
        asset_selector: ETH_USDC_SELECTOR,
      },
    });

    await clickButton(`Quick next ${ETH_NODE.node_id}`);
    expect(nthExpandRequest(1)).toEqual({
      seed_node_id: ETH_NODE.node_id,
      seed_lineage_id: ETH_NODE.lineage_id,
      operation_type: 'expand_next',
      options: {
        asset_selector: ETH_USDC_SELECTOR,
      },
    });

    await clickButton(`Select edge ${SAFE_EDGE.edge_id}`);
    await flushAsyncWork();
    await clickButton('Trace output only (scoped)');
    expect(nthExpandRequest(2)).toEqual({
      seed_node_id: ETH_COUNTERPARTY.node_id,
      seed_lineage_id: ETH_COUNTERPARTY.lineage_id,
      operation_type: 'expand_next',
      options: {
        tx_hashes: [SAFE_EDGE.tx_hash as string],
        asset_selector: ETH_USDC_SELECTOR,
      },
    });

    await clickButton(`Select edge ${UNSAFE_EDGE.edge_id}`);
    await flushAsyncWork();
    await clickButton('Trace input only');
    expect(nthExpandRequest(3)).toEqual({
      seed_node_id: ETH_NODE.node_id,
      seed_lineage_id: ETH_NODE.lineage_id,
      operation_type: 'expand_prev',
      options: {
        tx_hashes: [UNSAFE_EDGE.tx_hash as string],
      },
    });

    await clickButton(`Select node ${BTC_NODE.node_id}`);
    await flushAsyncWork();

    expect(querySelectByLabel('Asset scope')).toBeNull();
    expect(getAssetOptionsMock).toHaveBeenCalledTimes(1);

    await clickButton('Expand next');
    expect(nthExpandRequest(4)).toEqual({
      seed_node_id: BTC_NODE.node_id,
      seed_lineage_id: BTC_NODE.lineage_id,
      operation_type: 'expand_next',
      options: undefined,
    });
  });
});
