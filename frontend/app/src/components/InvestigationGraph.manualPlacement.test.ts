// @vitest-environment jsdom

import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import InvestigationGraph from './InvestigationGraph';
import { useGraphStore } from '../store/graphStore';
import type {
  AssetOption,
  ExpansionResponseV2,
  InvestigationEdge,
  InvestigationNode,
} from '../types/graph';

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
    chain: 'ethereum',
    display_label: address,
    expandable_directions: ['next', 'prev', 'neighbors'],
    address_data: {
      address,
      chain: 'ethereum',
    },
    ...overrides,
  };
}

function makeTransferEdge(
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

function makeExpandResponse(
  seedNodeId: string,
  nodes: InvestigationNode[],
  edges: InvestigationEdge[],
): ExpansionResponseV2 {
  return {
    session_id: 'sess-manual-placement',
    branch_id: 'branch-a',
    operation_id: 'op-expand-next',
    operation_type: 'expand_next',
    seed_node_id: seedNodeId,
    seed_lineage_id: 'lineage-seed',
    nodes,
    edges,
    added_nodes: nodes,
    added_edges: edges,
    updated_nodes: [],
    removed_node_ids: [],
    layout_hints: { suggested_layout: 'layered' },
    chain_context: { primary_chain: 'ethereum', chains_present: ['ethereum'] },
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

function findButtonByText(scope: ParentNode, text: string): HTMLButtonElement {
  const button = Array.from(scope.querySelectorAll('button')).find(
    (candidate) => candidate.textContent?.includes(text),
  );
  if (!button) {
    throw new Error(`Unable to find button containing text: ${text}`);
  }
  return button as HTMLButtonElement;
}

function findCheckboxByLabel(scope: ParentNode, text: string): HTMLInputElement {
  const label = Array.from(scope.querySelectorAll('label')).find(
    (candidate) => candidate.textContent?.includes(text),
  );
  const checkbox = label?.querySelector('input[type="checkbox"]');
  if (!checkbox) {
    throw new Error(`Unable to find checkbox containing text: ${text}`);
  }
  return checkbox as HTMLInputElement;
}

describe('InvestigationGraph manual placement persistence', () => {
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
      'sess-manual-placement',
      makeAddressNode('ethereum:address:0xseed', {
        path_id: 'path-seed',
        lineage_id: 'lineage-seed',
      }),
    );
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    useGraphStore.getState().reset();
  });

  it('keeps a manually moved node fixed after asset-scoped direct expand', async () => {
    const manualPosition = { x: 420, y: 180 };
    const childNode = makeAddressNode('ethereum:address:0xchild', {
      path_id: 'path-child',
      lineage_id: 'lineage-child',
      depth: 1,
      expandable_directions: ['prev'],
    });
    const assetOptions: AssetOption[] = [
      {
        mode: 'all',
        chain: 'ethereum',
        display_label: 'All assets',
      },
      {
        mode: 'asset',
        chain: 'ethereum',
        chain_asset_id: '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
        asset_symbol: 'USDC',
        display_label: 'USDC · 0xa0b8',
      },
    ];

    getAssetOptionsMock.mockResolvedValue({
      session_id: 'sess-manual-placement',
      seed_node_id: 'ethereum:address:0xseed',
      seed_lineage_id: 'lineage-seed',
      options: assetOptions,
    });
    expandNodeMock.mockResolvedValue(
      makeExpandResponse(
        'ethereum:address:0xseed',
        [childNode],
        [makeTransferEdge('edge-seed-child', 'ethereum:address:0xseed', childNode.node_id)],
      ),
    );

    useGraphStore.getState().syncRfPositions(
      [{ id: 'ethereum:address:0xseed', position: manualPosition }],
      { userInitiated: true },
    );

    await act(async () => {
      root.render(
        React.createElement(InvestigationGraph, {
          sessionId: 'sess-manual-placement',
          onStartNewInvestigation: () => undefined,
        }),
      );
    });

    const seedNode = findByTestId(container, 'rf-node-ethereum:address:0xseed');

    await act(async () => {
      seedNode.click();
    });

    await waitFor(() => {
      expect(getAssetOptionsMock).toHaveBeenCalledWith('sess-manual-placement', {
        seed_node_id: 'ethereum:address:0xseed',
        seed_lineage_id: 'lineage-seed',
      });
    });

    await waitFor(() => {
      expect(findCheckboxByLabel(container, 'USDC · 0xa0b8')).not.toBeNull();
    });

    const usdcOption = findCheckboxByLabel(container, 'USDC · 0xa0b8');

    await act(async () => {
      usdcOption.click();
    });

    await act(async () => {
      findButtonByText(seedNode, 'Next').click();
    });

    await waitFor(() => {
      expect(expandNodeMock).toHaveBeenCalledTimes(1);
      expect(expandNodeMock).toHaveBeenCalledWith(
        'sess-manual-placement',
        expect.objectContaining({
          seed_node_id: 'ethereum:address:0xseed',
          seed_lineage_id: 'lineage-seed',
          operation_type: 'expand_next',
          options: expect.objectContaining({
            asset_selectors: [expect.objectContaining({
              mode: 'asset',
              chain: 'ethereum',
              chain_asset_id: '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
              asset_symbol: 'USDC',
            })],
          }),
        }),
      );
    });

    await waitFor(() => {
      const state = useGraphStore.getState();
      const seedRfNode = state.rfNodes.find((node) => node.id === 'ethereum:address:0xseed');
      const childRfNode = state.rfNodes.find((node) => node.id === 'ethereum:address:0xchild');

      expect(seedRfNode?.position).toEqual(manualPosition);
      expect(state.layoutMetaMap.get('ethereum:address:0xseed')?.userPlaced).toBe(true);
      expect(childRfNode).toBeDefined();
      expect(childRfNode?.position).not.toEqual(manualPosition);
    });
  });
});
