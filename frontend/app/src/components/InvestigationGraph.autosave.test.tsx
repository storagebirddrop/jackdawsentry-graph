// @vitest-environment jsdom

import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import InvestigationGraph from './InvestigationGraph';
import { useGraphStore } from '../store/graphStore';
import type { AssetSelector, InvestigationNode } from '../types/graph';

const {
  MockApiError,
  expandNodeMock,
  getAssetOptionsMock,
  saveSessionSnapshotMock,
  saveWorkspaceMock,
} = vi.hoisted(() => {
  class MockApiError extends Error {
    status: number;

    constructor(status: number, message: string) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
    }
  }

  return {
    MockApiError,
    expandNodeMock: vi.fn(),
    getAssetOptionsMock: vi.fn(),
    saveSessionSnapshotMock: vi.fn(),
    saveWorkspaceMock: vi.fn(),
  };
});

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

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
  });
}

async function advanceAutosave(ms = 350): Promise<void> {
  await act(async () => {
    vi.advanceTimersByTime(ms);
    await Promise.resolve();
  });
}

describe('InvestigationGraph backend autosave', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers();
    expandNodeMock.mockReset();
    getAssetOptionsMock.mockReset();
    saveSessionSnapshotMock.mockReset();
    saveWorkspaceMock.mockReset();

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);

    useGraphStore.getState().reset();
    useGraphStore.getState().initSession(
      'sess-autosave',
      makeAddressNode('ethereum:address:0xaaa', 'ethereum'),
    );
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    useGraphStore.getState().reset();
    vi.useRealTimers();
  });

  it('posts debounced revisioned snapshots, advances revision after success, and includes nodeAssetScopes', async () => {
    const selector: AssetSelector = {
      mode: 'asset',
      chain: 'ethereum',
      chain_asset_id: '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
      asset_symbol: 'USDC',
    };

    saveSessionSnapshotMock
      .mockResolvedValueOnce({
        snapshot_id: 'snap-1',
        saved_at: '2026-04-13T10:00:00Z',
        revision: 5,
      })
      .mockResolvedValueOnce({
        snapshot_id: 'snap-2',
        saved_at: '2026-04-13T10:05:00Z',
        revision: 6,
      });

    await act(async () => {
      root.render(
        React.createElement(InvestigationGraph, {
          sessionId: 'sess-autosave',
          onStartNewInvestigation: () => undefined,
          initialWorkspaceRevision: 4,
        }),
      );
    });

    await advanceAutosave();
    expect(saveSessionSnapshotMock).not.toHaveBeenCalled();
    expect(saveWorkspaceMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      useGraphStore.getState().setNodeAssetScope('ethereum:address:0xaaa', [selector]);
    });

    await advanceAutosave();

    expect(saveSessionSnapshotMock).toHaveBeenCalledTimes(1);
    expect(saveSessionSnapshotMock).toHaveBeenLastCalledWith(
      'sess-autosave',
      expect.objectContaining({
        revision: 4,
        sessionId: 'sess-autosave',
        nodeAssetScopes: {
          'ethereum:address:0xaaa': [selector],
        },
      }),
    );

    await act(async () => {
      useGraphStore.getState().setNodeHidden('ethereum:address:0xaaa', true);
    });

    await advanceAutosave();

    expect(saveSessionSnapshotMock).toHaveBeenCalledTimes(2);
    expect(saveSessionSnapshotMock.mock.calls[1]?.[1]).toEqual(
      expect.objectContaining({
        revision: 5,
        sessionId: 'sess-autosave',
      }),
    );
  });

  it('pauses autosave on 409 conflicts, shows a stale notice, and keeps the local session hint updating', async () => {
    saveSessionSnapshotMock.mockRejectedValueOnce(
      new MockApiError(409, 'API 409: stale workspace snapshot revision'),
    );

    await act(async () => {
      root.render(
        React.createElement(InvestigationGraph, {
          sessionId: 'sess-autosave',
          onStartNewInvestigation: () => undefined,
          initialWorkspaceRevision: 7,
        }),
      );
    });

    await advanceAutosave();
    expect(saveSessionSnapshotMock).not.toHaveBeenCalled();

    await act(async () => {
      useGraphStore.getState().setNodeHidden('ethereum:address:0xaaa', true);
    });

    await advanceAutosave();
    await flush();

    expect(saveSessionSnapshotMock).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain('Autosave paused');
    expect(container.textContent).toContain('stale saved workspace revision');

    const localHintCallsAfterConflict = saveWorkspaceMock.mock.calls.length;

    await act(async () => {
      useGraphStore.getState().setNodeHidden('ethereum:address:0xaaa', false);
    });

    await advanceAutosave();
    await flush();

    expect(saveSessionSnapshotMock).toHaveBeenCalledTimes(1);
    expect(saveWorkspaceMock.mock.calls.length).toBeGreaterThan(localHintCallsAfterConflict);
  });
});
