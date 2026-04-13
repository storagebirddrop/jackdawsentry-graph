// @vitest-environment jsdom

import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import InvestigationGraph from './InvestigationGraph';
import { useGraphStore } from '../store/graphStore';
import type { AssetOption, ExpansionResponseV2, InvestigationNode } from '../types/graph';

const {
  MockApiError,
  expandNodeMock,
  getAssetOptionsMock,
  saveSessionSnapshotMock,
  saveWorkspaceMock,
} = vi.hoisted(() => ({
  MockApiError: class MockApiError extends Error {
    status = 0;
  },
  expandNodeMock: vi.fn(),
  getAssetOptionsMock: vi.fn(),
  saveSessionSnapshotMock: vi.fn(),
  saveWorkspaceMock: vi.fn(),
}));

vi.mock('../api/client', () => ({
  ApiError: MockApiError,
  expandNode: expandNodeMock,
  getAssetOptions: getAssetOptionsMock,
  saveSessionSnapshot: saveSessionSnapshotMock,
}));

vi.mock('../workspacePersistence', () => ({
  saveWorkspace: saveWorkspaceMock,
}));

vi.mock('../layout/elkLayout', () => {
  const defaultDimensions = { width: 320, height: 160 };
  return {
    computeElkLayout: vi.fn(async () => new Map<string, { x: number; y: number }>()),
    getNodeDimensions: vi.fn(() => defaultDimensions),
  };
});

vi.mock('@xyflow/react', async () => {
  const ReactModule = await import('react');

  function ReactFlow({
    nodes = [],
    edges = [],
    nodeTypes = {},
    onNodeClick,
    onEdgeClick,
    onPaneClick,
    onInit,
    children,
  }: Record<string, unknown>) {
    ReactModule.useEffect(() => {
      if (typeof onInit === 'function') {
        onInit({
          fitView: () => undefined,
          getNodes: () => nodes,
        });
      }
    }, [nodes, onInit]);

    return React.createElement(
      'div',
      {
        'data-testid': 'react-flow',
        onClick: () => {
          if (typeof onPaneClick === 'function') onPaneClick();
        },
      },
      ...(nodes as Array<Record<string, unknown>>).map((node) => {
        const NodeComponent = (nodeTypes as Record<string, React.ComponentType<Record<string, unknown>>>)[
          `${node.type ?? ''}`
        ];
        return React.createElement(
          'div',
          {
            key: `${node.id ?? ''}`,
            'data-testid': `rf-node-${node.id ?? ''}`,
            onClick: (event: MouseEvent) => {
              event.stopPropagation();
              if (typeof onNodeClick === 'function') onNodeClick(event, node);
            },
          },
          NodeComponent
            ? React.createElement(NodeComponent, {
                id: node.id,
                data: node.data,
                selected: false,
              })
            : null,
        );
      }),
      ...(edges as Array<Record<string, unknown>>).map((edge) =>
        React.createElement('div', {
          key: `${edge.id ?? ''}`,
          'data-testid': `rf-edge-${edge.id ?? ''}`,
          onClick: (event: MouseEvent) => {
            event.stopPropagation();
            if (typeof onEdgeClick === 'function') onEdgeClick(event, edge);
          },
        })),
      children as React.ReactNode,
    );
  }

  return {
    ReactFlow,
    Background: () => null,
    Controls: () => null,
    MiniMap: () => null,
    Handle: () => null,
    Position: {
      Left: 'left',
      Right: 'right',
      Top: 'top',
      Bottom: 'bottom',
    },
    BackgroundVariant: {
      Dots: 'dots',
    },
    MarkerType: {
      ArrowClosed: 'arrowclosed',
    },
    useNodesState<T>(initialNodes: T[]) {
      const [nodes, setNodes] = ReactModule.useState(initialNodes);
      return [nodes, setNodes, vi.fn()] as const;
    },
    useEdgesState<T>(initialEdges: T[]) {
      const [edges, setEdges] = ReactModule.useState(initialEdges);
      return [edges, setEdges, vi.fn()] as const;
    },
  };
});

function makeAddressNode(
  id: string,
  chain: string,
  overrides: Partial<InvestigationNode> = {},
): InvestigationNode {
  const address = id.split(':').slice(2).join(':');
  return {
    node_id: id,
    node_type: 'address',
    branch_id: 'branch-a',
    path_id: `path-${id}`,
    lineage_id: `lineage-${id}`,
    depth: 0,
    chain,
    display_label: address,
    expandable_directions: ['next', 'prev', 'neighbors'],
    address_data: {
      address,
      chain,
    },
    ...overrides,
  };
}

function makeDelta(nodes: InvestigationNode[]): ExpansionResponseV2 {
  return {
    session_id: 'sess-selector',
    branch_id: 'branch-a',
    operation_id: 'op-seed-neighbors',
    operation_type: 'expand_neighbors',
    seed_node_id: 'ethereum:address:0xaaa',
    seed_lineage_id: 'lineage-ethereum:address:0xaaa',
    nodes,
    edges: [],
    added_nodes: nodes,
    added_edges: [],
    updated_nodes: [],
    removed_node_ids: [],
    layout_hints: { suggested_layout: 'layered' },
    chain_context: { primary_chain: 'ethereum', chains_present: ['ethereum', 'solana', 'bitcoin'] },
  };
}

async function flush(): Promise<void> {
  await act(async () => {
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

function findByTestId(container: HTMLElement, testId: string): HTMLElement {
  const element = container.querySelector(`[data-testid="${testId}"]`);
  if (!element) {
    throw new Error(`Unable to find element with test id ${testId}`);
  }
  return element as HTMLElement;
}

function queryLabeledInput(
  container: HTMLElement,
  label: string,
  inputType: 'radio' | 'checkbox',
): HTMLInputElement | null {
  const matchingLabel = Array.from(container.querySelectorAll('label')).find(
    (candidate) => candidate.textContent?.trim() === label,
  );
  return (matchingLabel?.querySelector(`input[type="${inputType}"]`) as HTMLInputElement | null) ?? null;
}

function getAssetModeRadio(container: HTMLElement, label: 'All assets' | 'Specific assets'): HTMLInputElement {
  const radio = queryLabeledInput(container, label, 'radio');
  if (!radio) {
    throw new Error(`Unable to find asset-scope radio "${label}"`);
  }
  return radio;
}

function getAssetCheckbox(container: HTMLElement, label: string): HTMLInputElement {
  const checkbox = queryLabeledInput(container, label, 'checkbox');
  if (!checkbox) {
    throw new Error(`Unable to find asset checkbox "${label}"`);
  }
  return checkbox;
}

function getButtonByText(container: HTMLElement, label: string): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll('button')).find(
    (candidate) => candidate.textContent?.trim() === label,
  );
  if (!button) {
    throw new Error(`Unable to find button "${label}"`);
  }
  return button as HTMLButtonElement;
}

function getButtonByTextWithin(container: HTMLElement, label: string): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll('button')).find(
    (candidate) => candidate.textContent?.trim() === label,
  );
  if (!button) {
    throw new Error(`Unable to find nested button "${label}"`);
  }
  return button as HTMLButtonElement;
}

async function clickInput(input: HTMLInputElement): Promise<void> {
  await act(async () => {
    input.click();
  });
}

describe('InvestigationGraph asset selector persistence', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    expandNodeMock.mockReset();
    getAssetOptionsMock.mockReset();
    saveSessionSnapshotMock.mockReset();
    saveWorkspaceMock.mockReset();
    saveSessionSnapshotMock.mockResolvedValue({
      snapshot_id: 'snap-selector',
      revision: 1,
    });

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);

    useGraphStore.getState().reset();
    useGraphStore.getState().initSession(
      'sess-selector',
      makeAddressNode('ethereum:address:0xaaa', 'ethereum'),
    );
    useGraphStore.getState().applyExpansionDelta(
      makeDelta([
        makeAddressNode('solana:address:So11111111111111111111111111111111111111112', 'solana'),
        makeAddressNode('bitcoin:address:bc1qexampleaddress0000000000000000000000000', 'bitcoin'),
      ]),
    );
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    useGraphStore.getState().reset();
  });

  it('loads per-node asset options, restores stored selections on reselection, and skips Bitcoin', async () => {
    const nodeAOptions: AssetOption[] = [
      { mode: 'all', chain: 'ethereum', display_label: 'All assets' },
      {
        mode: 'asset',
        chain: 'ethereum',
        chain_asset_id: '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
        asset_symbol: 'USDC',
        display_label: 'USDC · Ethereum',
      },
    ];
    const nodeBOptions: AssetOption[] = [
      { mode: 'all', chain: 'solana', display_label: 'All assets' },
      {
        mode: 'asset',
        chain: 'solana',
        chain_asset_id: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
        asset_symbol: 'USDC',
        display_label: 'USDC · Solana',
      },
      {
        mode: 'native',
        chain: 'solana',
        asset_symbol: 'SOL',
        display_label: 'Native SOL',
      },
    ];

    getAssetOptionsMock.mockImplementation(async (_sessionId: string, request: { seed_node_id: string }) => {
      if (request.seed_node_id === 'ethereum:address:0xaaa') {
        return {
          session_id: 'sess-selector',
          seed_node_id: request.seed_node_id,
          seed_lineage_id: 'lineage-ethereum:address:0xaaa',
          options: nodeAOptions,
        };
      }
      if (request.seed_node_id === 'solana:address:So11111111111111111111111111111111111111112') {
        return {
          session_id: 'sess-selector',
          seed_node_id: request.seed_node_id,
          seed_lineage_id: 'lineage-solana:address:So11111111111111111111111111111111111111112',
          options: nodeBOptions,
        };
      }
      throw new Error(`Unexpected asset-options request for ${request.seed_node_id}`);
    });

    await act(async () => {
      root.render(
        React.createElement(InvestigationGraph, {
          sessionId: 'sess-selector',
          onStartNewInvestigation: () => undefined,
        }),
      );
    });

    await act(async () => {
      findByTestId(container, 'rf-node-ethereum:address:0xaaa').click();
    });

    await waitFor(() => {
      expect(getAssetOptionsMock).toHaveBeenCalledWith('sess-selector', {
        seed_node_id: 'ethereum:address:0xaaa',
        seed_lineage_id: 'lineage-ethereum:address:0xaaa',
      });
    });

    await waitFor(() => {
      expect(getAssetModeRadio(container, 'All assets').checked).toBe(true);
      expect(getAssetModeRadio(container, 'Specific assets').checked).toBe(false);
      expect(queryLabeledInput(container, 'USDC · Ethereum', 'checkbox')).toBeNull();
    });

    await clickInput(getAssetModeRadio(container, 'Specific assets'));
    await clickInput(getAssetCheckbox(container, 'USDC · Ethereum'));

    expect(getAssetModeRadio(container, 'Specific assets').checked).toBe(true);
    expect(getAssetCheckbox(container, 'USDC · Ethereum').checked).toBe(true);

    await act(async () => {
      findByTestId(container, 'rf-node-solana:address:So11111111111111111111111111111111111111112').click();
    });

    await waitFor(() => {
      expect(getAssetOptionsMock).toHaveBeenCalledWith('sess-selector', {
        seed_node_id: 'solana:address:So11111111111111111111111111111111111111112',
        seed_lineage_id: 'lineage-solana:address:So11111111111111111111111111111111111111112',
      });
    });

    await waitFor(() => {
      expect(getAssetModeRadio(container, 'All assets').checked).toBe(true);
      expect(getAssetModeRadio(container, 'Specific assets').checked).toBe(false);
      expect(queryLabeledInput(container, 'USDC · Solana', 'checkbox')).toBeNull();
      expect(queryLabeledInput(container, 'Native SOL', 'checkbox')).toBeNull();
    });

    await clickInput(getAssetModeRadio(container, 'Specific assets'));
    await clickInput(getAssetCheckbox(container, 'Native SOL'));

    expect(getAssetModeRadio(container, 'Specific assets').checked).toBe(true);
    expect(getAssetCheckbox(container, 'Native SOL').checked).toBe(true);

    await act(async () => {
      findByTestId(container, 'rf-node-ethereum:address:0xaaa').click();
    });

    await waitFor(() => {
      expect(getAssetModeRadio(container, 'Specific assets').checked).toBe(true);
      expect(getAssetCheckbox(container, 'USDC · Ethereum').checked).toBe(true);
    });

    await act(async () => {
      findByTestId(container, 'rf-node-solana:address:So11111111111111111111111111111111111111112').click();
    });

    await waitFor(() => {
      expect(getAssetModeRadio(container, 'Specific assets').checked).toBe(true);
      expect(getAssetCheckbox(container, 'Native SOL').checked).toBe(true);
    });

    await act(async () => {
      findByTestId(container, 'rf-node-bitcoin:address:bc1qexampleaddress0000000000000000000000000').click();
    });

    await waitFor(() => {
      expect(queryLabeledInput(container, 'All assets', 'radio')).toBeNull();
    });

    expect(getAssetOptionsMock).toHaveBeenCalledTimes(2);
    expect(getAssetOptionsMock.mock.calls.map((call) => call[1]?.seed_node_id)).toEqual([
      'ethereum:address:0xaaa',
      'solana:address:So11111111111111111111111111111111111111112',
    ]);
  });

  it('disambiguates repeated asset labels with shortened chain-local identity', async () => {
    getAssetOptionsMock.mockImplementation(async (_sessionId: string, request: { seed_node_id: string }) => {
      if (request.seed_node_id !== 'ethereum:address:0xaaa') {
        throw new Error(`Unexpected asset-options request for ${request.seed_node_id}`);
      }
      return {
        session_id: 'sess-selector',
        seed_node_id: request.seed_node_id,
        seed_lineage_id: 'lineage-ethereum:address:0xaaa',
        options: [
          { mode: 'all', chain: 'ethereum', display_label: 'All assets' },
          {
            mode: 'asset',
            chain: 'ethereum',
            chain_asset_id: '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
            asset_symbol: 'USDC',
            display_label: 'USDC',
          },
          {
            mode: 'asset',
            chain: 'ethereum',
            chain_asset_id: '0x1234567890abcdef1234567890abcdef12345678',
            asset_symbol: 'USDC',
            display_label: 'USDC',
          },
        ] satisfies AssetOption[],
      };
    });

    await act(async () => {
      root.render(
        React.createElement(InvestigationGraph, {
          sessionId: 'sess-selector',
          onStartNewInvestigation: () => undefined,
        }),
      );
    });

    await act(async () => {
      findByTestId(container, 'rf-node-ethereum:address:0xaaa').click();
    });

    await waitFor(() => {
      expect(getAssetModeRadio(container, 'All assets').checked).toBe(true);
    });

    await clickInput(getAssetModeRadio(container, 'Specific assets'));

    await waitFor(() => {
      expect(queryLabeledInput(container, 'USDC · 0xa0b869...eb48', 'checkbox')).not.toBeNull();
      expect(queryLabeledInput(container, 'USDC · 0x123456...5678', 'checkbox')).not.toBeNull();
    });
  });

  it('round-trips manual export/import and restores the selected asset scope in the active UI', async () => {
    getAssetOptionsMock.mockResolvedValue({
      session_id: 'sess-selector',
      seed_node_id: 'ethereum:address:0xaaa',
      seed_lineage_id: 'lineage-ethereum:address:0xaaa',
      options: [
        { mode: 'all', chain: 'ethereum', display_label: 'All assets' },
        {
          mode: 'asset',
          chain: 'ethereum',
          chain_asset_id: '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
          asset_symbol: 'USDC',
          display_label: 'USDC · Ethereum',
        },
      ] satisfies AssetOption[],
    });

    await act(async () => {
      root.render(
        React.createElement(InvestigationGraph, {
          sessionId: 'sess-selector',
          onStartNewInvestigation: () => undefined,
        }),
      );
    });

    await act(async () => {
      findByTestId(container, 'rf-node-ethereum:address:0xaaa').click();
    });

    await waitFor(() => {
      expect(getAssetModeRadio(container, 'All assets').checked).toBe(true);
    });

    await clickInput(getAssetModeRadio(container, 'Specific assets'));
    await clickInput(getAssetCheckbox(container, 'USDC · Ethereum'));

    const snapshot = useGraphStore.getState().exportSnapshot();

    await act(async () => {
      useGraphStore.getState().setNodeAssetScope('ethereum:address:0xaaa', null);
    });

    await waitFor(() => {
      expect(getAssetModeRadio(container, 'All assets').checked).toBe(true);
      expect(getAssetModeRadio(container, 'Specific assets').checked).toBe(false);
    });

    await act(async () => {
      useGraphStore.getState().importSnapshot(snapshot);
    });

    await waitFor(() => {
      expect(getAssetModeRadio(container, 'Specific assets').checked).toBe(true);
      expect(getAssetCheckbox(container, 'USDC · Ethereum').checked).toBe(true);
    });
  });

  it('uses restored stored selectors for quick expand, direct expand, and preview after importSnapshot restore', async () => {
    const selector = {
      mode: 'asset' as const,
      chain: 'ethereum',
      chain_asset_id: '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
      asset_symbol: 'USDC',
    };

    getAssetOptionsMock.mockResolvedValue({
      session_id: 'sess-selector',
      seed_node_id: 'ethereum:address:0xaaa',
      seed_lineage_id: 'lineage-ethereum:address:0xaaa',
      options: [
        { mode: 'all', chain: 'ethereum', display_label: 'All assets' },
        {
          ...selector,
          display_label: 'USDC · Ethereum',
        },
      ] satisfies AssetOption[],
    });
    expandNodeMock.mockResolvedValue(makeDelta([]));

    useGraphStore.getState().setNodeAssetScope('ethereum:address:0xaaa', [selector]);
    const snapshot = useGraphStore.getState().exportSnapshot();
    useGraphStore.getState().reset();
    useGraphStore.getState().importSnapshot(snapshot);

    await act(async () => {
      root.render(
        React.createElement(InvestigationGraph, {
          sessionId: 'sess-selector',
          onStartNewInvestigation: () => undefined,
        }),
      );
    });

    await act(async () => {
      getButtonByTextWithin(
        findByTestId(container, 'rf-node-ethereum:address:0xaaa'),
        'Next →',
      ).click();
    });

    await waitFor(() => {
      expect(expandNodeMock).toHaveBeenCalledWith('sess-selector', expect.objectContaining({
        operation_type: 'expand_next',
        seed_node_id: 'ethereum:address:0xaaa',
        options: expect.objectContaining({
          asset_selectors: [selector],
        }),
      }));
    });

    expandNodeMock.mockClear();

    await act(async () => {
      findByTestId(container, 'rf-node-ethereum:address:0xaaa').click();
    });

    await waitFor(() => {
      expect(getAssetModeRadio(container, 'Specific assets').checked).toBe(true);
    });

    await act(async () => {
      getButtonByText(container, 'Expand next').click();
    });

    await waitFor(() => {
      expect(expandNodeMock).toHaveBeenCalledWith('sess-selector', expect.objectContaining({
        operation_type: 'expand_next',
        seed_node_id: 'ethereum:address:0xaaa',
        options: expect.objectContaining({
          asset_selectors: [selector],
        }),
      }));
    });

    expandNodeMock.mockClear();

    await act(async () => {
      getButtonByText(container, 'Filter & Preview ▼').click();
    });
    await act(async () => {
      getButtonByText(container, 'Preview next').click();
    });

    await waitFor(() => {
      expect(expandNodeMock).toHaveBeenCalledWith('sess-selector', expect.objectContaining({
        operation_type: 'expand_next',
        seed_node_id: 'ethereum:address:0xaaa',
        options: expect.objectContaining({
          asset_selectors: [selector],
          max_results: 25,
        }),
      }));
    });
  });

  it('restores explicit zero-selection specific mode and keeps expand actions blocked', async () => {
    getAssetOptionsMock.mockResolvedValue({
      session_id: 'sess-selector',
      seed_node_id: 'ethereum:address:0xaaa',
      seed_lineage_id: 'lineage-ethereum:address:0xaaa',
      options: [
        { mode: 'all', chain: 'ethereum', display_label: 'All assets' },
        {
          mode: 'asset',
          chain: 'ethereum',
          chain_asset_id: '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
          asset_symbol: 'USDC',
          display_label: 'USDC · Ethereum',
        },
      ] satisfies AssetOption[],
    });

    const snapshot = JSON.stringify({
      sessionId: 'sess-selector',
      nodes: Array.from(useGraphStore.getState().nodeMap.values()),
      edges: [],
      positions: {},
      branches: Array.from(useGraphStore.getState().branchMap.values()),
      nodeAssetScopes: {
        'ethereum:address:0xaaa': [],
      },
    });
    useGraphStore.getState().reset();
    useGraphStore.getState().importSnapshot(snapshot);

    await act(async () => {
      root.render(
        React.createElement(InvestigationGraph, {
          sessionId: 'sess-selector',
          onStartNewInvestigation: () => undefined,
        }),
      );
    });

    await act(async () => {
      findByTestId(container, 'rf-node-ethereum:address:0xaaa').click();
    });

    await waitFor(() => {
      expect(getAssetModeRadio(container, 'Specific assets').checked).toBe(true);
    });

    expect(getButtonByText(container, 'Expand next').disabled).toBe(true);

    await act(async () => {
      getButtonByText(container, 'Filter & Preview ▼').click();
    });
    expect(getButtonByText(container, 'Preview next').disabled).toBe(true);

    await act(async () => {
      getButtonByTextWithin(
        findByTestId(container, 'rf-node-ethereum:address:0xaaa'),
        'Next →',
      ).click();
    });
    expect(expandNodeMock).not.toHaveBeenCalled();
  });

  it('ignores Bitcoin nodeAssetScopes present in an imported snapshot', async () => {
    expandNodeMock.mockResolvedValue(makeDelta([]));

    const snapshot = JSON.stringify({
      sessionId: 'sess-selector',
      nodes: Array.from(useGraphStore.getState().nodeMap.values()),
      edges: [],
      positions: {},
      branches: Array.from(useGraphStore.getState().branchMap.values()),
      nodeAssetScopes: {
        'bitcoin:address:bc1qexampleaddress0000000000000000000000000': [{
          mode: 'native',
          chain: 'bitcoin',
          asset_symbol: 'BTC',
        }],
      },
    });
    useGraphStore.getState().reset();
    useGraphStore.getState().importSnapshot(snapshot);

    await act(async () => {
      root.render(
        React.createElement(InvestigationGraph, {
          sessionId: 'sess-selector',
          onStartNewInvestigation: () => undefined,
        }),
      );
    });

    expect(
      useGraphStore.getState().nodeAssetScopes.has(
        'bitcoin:address:bc1qexampleaddress0000000000000000000000000',
      ),
    ).toBe(false);

    await act(async () => {
      getButtonByTextWithin(
        findByTestId(container, 'rf-node-bitcoin:address:bc1qexampleaddress0000000000000000000000000'),
        'Next →',
      ).click();
    });

    await waitFor(() => {
      expect(expandNodeMock).toHaveBeenCalledWith('sess-selector', expect.objectContaining({
        seed_node_id: 'bitcoin:address:bc1qexampleaddress0000000000000000000000000',
      }));
    });
    const request = expandNodeMock.mock.calls[0]?.[1];
    expect(request?.options?.asset_selectors).toBeUndefined();
  });
});
