// @vitest-environment jsdom

import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import InvestigationGraph from './InvestigationGraph';
import { useGraphStore } from '../store/graphStore';
import type { AssetOption, ExpansionResponseV2, InvestigationNode } from '../types/graph';

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

function getAssetScopeSelect(container: HTMLElement): HTMLSelectElement | null {
  return container.querySelector('select');
}

function selectOptionByLabel(select: HTMLSelectElement, label: string): void {
  const option = Array.from(select.options).find((candidate) => candidate.textContent === label);
  if (!option) {
    throw new Error(`Unable to find option with label ${label}`);
  }
  select.value = option.value;
  select.dispatchEvent(new Event('change', { bubbles: true }));
}

function selectedOptionLabel(select: HTMLSelectElement): string {
  return select.options[select.selectedIndex]?.textContent ?? '';
}

describe('InvestigationGraph asset selector persistence', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    expandNodeMock.mockReset();
    getAssetOptionsMock.mockReset();
    saveWorkspaceMock.mockReset();

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
      const select = getAssetScopeSelect(container);
      expect(select).not.toBeNull();
      expect(Array.from(select?.options ?? []).map((option) => option.textContent)).toEqual([
        'All assets',
        'USDC · Ethereum',
      ]);
      expect(selectedOptionLabel(select as HTMLSelectElement)).toBe('All assets');
      return select as HTMLSelectElement;
    });

    await act(async () => {
      selectOptionByLabel(getAssetScopeSelect(container) as HTMLSelectElement, 'USDC · Ethereum');
    });

    expect(selectedOptionLabel(getAssetScopeSelect(container) as HTMLSelectElement)).toBe('USDC · Ethereum');

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
      const select = getAssetScopeSelect(container);
      expect(select).not.toBeNull();
      expect(Array.from(select?.options ?? []).map((option) => option.textContent)).toEqual([
        'All assets',
        'USDC · Solana',
        'Native SOL',
      ]);
      expect(selectedOptionLabel(select as HTMLSelectElement)).toBe('All assets');
      return select as HTMLSelectElement;
    });

    await act(async () => {
      selectOptionByLabel(getAssetScopeSelect(container) as HTMLSelectElement, 'Native SOL');
    });

    expect(selectedOptionLabel(getAssetScopeSelect(container) as HTMLSelectElement)).toBe('Native SOL');

    await act(async () => {
      findByTestId(container, 'rf-node-ethereum:address:0xaaa').click();
    });

    await waitFor(() => {
      const select = getAssetScopeSelect(container);
      expect(select).not.toBeNull();
      expect(selectedOptionLabel(select as HTMLSelectElement)).toBe('USDC · Ethereum');
      return select as HTMLSelectElement;
    });

    await act(async () => {
      findByTestId(container, 'rf-node-solana:address:So11111111111111111111111111111111111111112').click();
    });

    await waitFor(() => {
      const select = getAssetScopeSelect(container);
      expect(select).not.toBeNull();
      expect(selectedOptionLabel(select as HTMLSelectElement)).toBe('Native SOL');
      return select as HTMLSelectElement;
    });

    await act(async () => {
      findByTestId(container, 'rf-node-bitcoin:address:bc1qexampleaddress0000000000000000000000000').click();
    });

    await waitFor(() => {
      expect(getAssetScopeSelect(container)).toBeNull();
    });

    expect(getAssetOptionsMock).toHaveBeenCalledTimes(2);
    expect(getAssetOptionsMock.mock.calls.map((call) => call[1]?.seed_node_id)).toEqual([
      'ethereum:address:0xaaa',
      'solana:address:So11111111111111111111111111111111111111112',
    ]);
  });
});
