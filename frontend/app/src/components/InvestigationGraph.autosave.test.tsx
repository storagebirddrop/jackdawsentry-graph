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
  getSessionMock,
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
    getSessionMock: vi.fn(),
    saveSessionSnapshotMock: vi.fn(),
    saveWorkspaceMock: vi.fn(),
  };
});

vi.mock('../api/client', () => ({
  ApiError: MockApiError,
  expandNode: expandNodeMock,
  getAssetOptions: getAssetOptionsMock,
  getSession: getSessionMock,
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
    getSessionMock.mockReset();
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
    expect(container.textContent).toContain('Autosave conflict');
    expect(container.textContent).toContain('Another tab or session saved a newer version');

    const localHintCallsAfterConflict = saveWorkspaceMock.mock.calls.length;

    await act(async () => {
      useGraphStore.getState().setNodeHidden('ethereum:address:0xaaa', false);
    });

    await advanceAutosave();
    await flush();

    expect(saveSessionSnapshotMock).toHaveBeenCalledTimes(1);
    expect(saveWorkspaceMock.mock.calls.length).toBeGreaterThan(localHintCallsAfterConflict);
  });

  it('keeps autosave paused across further local edits until a recovery action succeeds, while local hint continues', async () => {
    saveSessionSnapshotMock.mockRejectedValueOnce(
      new MockApiError(409, 'stale'),
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

    // Trigger the 409.
    await act(async () => {
      useGraphStore.getState().setNodeHidden('ethereum:address:0xaaa', true);
    });
    await advanceAutosave();
    await flush();

    expect(saveSessionSnapshotMock).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain('Autosave conflict');

    const localCallsAfterConflict = saveWorkspaceMock.mock.calls.length;

    // Make several more edits — backend autosave must stay paused.
    for (let i = 0; i < 3; i++) {
      await act(async () => {
        useGraphStore.getState().setNodeHidden(
          'ethereum:address:0xaaa',
          i % 2 === 0 ? false : true,
        );
      });
      await advanceAutosave();
      await flush();
    }

    // Backend save was never called again.
    expect(saveSessionSnapshotMock).toHaveBeenCalledTimes(1);
    // But local hint continued updating.
    expect(saveWorkspaceMock.mock.calls.length).toBeGreaterThan(localCallsAfterConflict);
    // Conflict banner is still visible.
    expect(container.textContent).toContain('Autosave conflict');
    expect(container.textContent).toContain('Save my version');
    expect(container.textContent).toContain('Load saved version');
  });

  it('recovers via force-save: fetches server revision, saves current state, and resumes autosave', async () => {
    saveSessionSnapshotMock.mockRejectedValueOnce(
      new MockApiError(409, 'stale'),
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

    // Trigger conflict.
    await act(async () => {
      useGraphStore.getState().setNodeHidden('ethereum:address:0xaaa', true);
    });
    await advanceAutosave();
    await flush();

    expect(container.textContent).toContain('Autosave conflict');

    // Set up mocks for force-save recovery.
    getSessionMock.mockResolvedValueOnce({
      session_id: 'sess-autosave',
      workspace: {
        schema_version: 1,
        revision: 10,
        sessionId: 'sess-autosave',
        nodes: [],
        edges: [],
        positions: {},
      },
      restore_state: 'full',
      nodes: [],
      edges: [],
      branch_map: {},
      snapshot_saved_at: '2026-04-13T12:00:00Z',
    });
    saveSessionSnapshotMock.mockResolvedValueOnce({
      snapshot_id: 'snap-force',
      saved_at: '2026-04-13T12:01:00Z',
      revision: 10,
    });

    // Click "Save my version".
    const saveBtn = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent === 'Save my version',
    );
    expect(saveBtn).toBeDefined();
    await act(async () => {
      saveBtn!.click();
    });
    await flush();

    // Conflict banner gone, success notice shown.
    expect(container.textContent).not.toContain('Autosave conflict');
    expect(container.textContent).not.toContain('Autosave paused');
    expect(container.textContent).toContain('Autosave resumed');

    // Autosave resumes: next edit triggers a backend save.
    saveSessionSnapshotMock.mockResolvedValueOnce({
      snapshot_id: 'snap-resumed',
      saved_at: '2026-04-13T12:02:00Z',
      revision: 11,
    });
    await act(async () => {
      useGraphStore.getState().setNodeHidden('ethereum:address:0xaaa', false);
    });
    await advanceAutosave();
    await flush();

    // The initial 409 call + the force-save call + the resumed autosave call = 3.
    expect(saveSessionSnapshotMock).toHaveBeenCalledTimes(3);
  });

  it('recovers via reload: fetches server snapshot, imports it, and resumes autosave', async () => {
    saveSessionSnapshotMock.mockRejectedValueOnce(
      new MockApiError(409, 'stale'),
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

    // Trigger conflict.
    await act(async () => {
      useGraphStore.getState().setNodeHidden('ethereum:address:0xaaa', true);
    });
    await advanceAutosave();
    await flush();

    expect(container.textContent).toContain('Autosave conflict');

    // Set up mock for reload.
    const serverNode = makeAddressNode('ethereum:address:0xbbb', 'ethereum');
    getSessionMock.mockResolvedValueOnce({
      session_id: 'sess-autosave',
      workspace: {
        schema_version: 1,
        revision: 10,
        sessionId: 'sess-autosave',
        nodes: [serverNode],
        edges: [],
        positions: { [serverNode.node_id]: { x: 100, y: 200 } },
      },
      restore_state: 'full',
      nodes: [serverNode],
      edges: [],
      branch_map: {},
      snapshot_saved_at: '2026-04-13T12:00:00Z',
    });

    // Mock window.confirm to return true.
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValueOnce(true);

    // Click "Load saved version".
    const loadBtn = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent === 'Load saved version',
    );
    expect(loadBtn).toBeDefined();
    await act(async () => {
      loadBtn!.click();
    });
    await flush();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    confirmSpy.mockRestore();

    // Conflict banner gone, success notice shown.
    expect(container.textContent).not.toContain('Autosave conflict');
    expect(container.textContent).not.toContain('Autosave paused');
    expect(container.textContent).toContain('Autosave resumed');

    // Graph state was replaced with the server snapshot.
    expect(useGraphStore.getState().rfNodes).toHaveLength(1);
    expect(useGraphStore.getState().rfNodes[0]!.id).toBe('ethereum:address:0xbbb');
  });

  it('does nothing when user cancels the reload confirmation dialog', async () => {
    saveSessionSnapshotMock.mockRejectedValueOnce(
      new MockApiError(409, 'stale'),
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

    await act(async () => {
      useGraphStore.getState().setNodeHidden('ethereum:address:0xaaa', true);
    });
    await advanceAutosave();
    await flush();

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValueOnce(false);

    const loadBtn = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent === 'Load saved version',
    );
    await act(async () => {
      loadBtn!.click();
    });
    await flush();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    confirmSpy.mockRestore();

    // Banner still visible, no getSession call made.
    expect(container.textContent).toContain('Autosave conflict');
    expect(getSessionMock).not.toHaveBeenCalled();
  });

  it('handles force-save race (second 409) gracefully and stays in conflict state', async () => {
    saveSessionSnapshotMock.mockRejectedValueOnce(
      new MockApiError(409, 'stale'),
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

    await act(async () => {
      useGraphStore.getState().setNodeHidden('ethereum:address:0xaaa', true);
    });
    await advanceAutosave();
    await flush();

    // Server fetch succeeds, but the subsequent save hits another 409.
    getSessionMock.mockResolvedValueOnce({
      session_id: 'sess-autosave',
      workspace: {
        schema_version: 1,
        revision: 10,
        sessionId: 'sess-autosave',
        nodes: [],
        edges: [],
        positions: {},
      },
      restore_state: 'full',
      nodes: [],
      edges: [],
      branch_map: {},
    });
    saveSessionSnapshotMock.mockRejectedValueOnce(
      new MockApiError(409, 'stale again'),
    );

    const saveBtn = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent === 'Save my version',
    );
    await act(async () => {
      saveBtn!.click();
    });
    await flush();

    // Conflict banner stays, transient error shown.
    expect(container.textContent).toContain('Autosave conflict');
    expect(container.textContent).toContain('Another save occurred');
  });

  it('disables both recovery buttons while an action is in flight', async () => {
    saveSessionSnapshotMock.mockRejectedValueOnce(
      new MockApiError(409, 'stale'),
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

    await act(async () => {
      useGraphStore.getState().setNodeHidden('ethereum:address:0xaaa', true);
    });
    await advanceAutosave();
    await flush();

    // Make getSession hang (never resolve) to keep the action in flight.
    getSessionMock.mockReturnValueOnce(new Promise(() => {}));

    const saveBtn = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent === 'Save my version',
    );
    await act(async () => {
      saveBtn!.click();
    });

    // Both buttons should now be disabled.
    const buttons = Array.from(container.querySelectorAll('button')).filter(
      (b) => b.textContent === 'Saving\u2026' || b.textContent === 'Load saved version',
    );
    expect(buttons).toHaveLength(2);
    for (const btn of buttons) {
      expect(btn.disabled).toBe(true);
    }
  });

  it('shows error notice and stays in conflict state when getSession fails during force-save', async () => {
    saveSessionSnapshotMock.mockRejectedValueOnce(
      new MockApiError(409, 'stale'),
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

    await act(async () => {
      useGraphStore.getState().setNodeHidden('ethereum:address:0xaaa', true);
    });
    await advanceAutosave();
    await flush();

    getSessionMock.mockRejectedValueOnce(new Error('network error'));

    const saveBtn = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent === 'Save my version',
    );
    await act(async () => {
      saveBtn!.click();
    });
    await flush();

    // Conflict banner stays, error notice appears, buttons re-enabled.
    expect(container.textContent).toContain('Autosave conflict');
    expect(container.textContent).toContain('Unable to save your version');
    const retryBtn = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent === 'Save my version',
    );
    expect(retryBtn).toBeDefined();
    expect(retryBtn!.disabled).toBe(false);
  });
});
