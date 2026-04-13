// @vitest-environment jsdom

import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App';
import { useGraphStore } from './store/graphStore';
import type {
  AssetSelector,
  ExpandRequest,
  ExpansionResponseV2,
  InvestigationNode,
  InvestigationSessionResponse,
  RecentSessionSummary,
  WorkspaceSnapshotV1,
} from './types/graph';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const {
  MockApiError,
  createSessionMock,
  expandNodeMock,
  getAssetOptionsMock,
  getRecentSessionsMock,
  getSessionMock,
  isAuthenticatedMock,
  redirectToLoginMock,
  saveSessionSnapshotMock,
  clearSavedWorkspaceMock,
  saveSessionWorkspacePreferencesMock,
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
    createSessionMock: vi.fn(),
    expandNodeMock: vi.fn(),
    getAssetOptionsMock: vi.fn(),
    getRecentSessionsMock: vi.fn(),
    getSessionMock: vi.fn(),
    isAuthenticatedMock: vi.fn(),
    redirectToLoginMock: vi.fn(),
    saveSessionSnapshotMock: vi.fn(),
    clearSavedWorkspaceMock: vi.fn(),
    saveSessionWorkspacePreferencesMock: vi.fn(),
    saveWorkspaceMock: vi.fn(),
  };
});

vi.mock('./api/client', () => ({
  ApiError: MockApiError,
  createSession: createSessionMock,
  expandNode: expandNodeMock,
  getAssetOptions: getAssetOptionsMock,
  getRecentSessions: getRecentSessionsMock,
  getSession: getSessionMock,
  isAuthenticated: isAuthenticatedMock,
  redirectToLogin: redirectToLoginMock,
  saveSessionSnapshot: saveSessionSnapshotMock,
}));

vi.mock('./workspacePersistence', () => ({
  clearSavedWorkspace: clearSavedWorkspaceMock,
  saveSessionWorkspacePreferences: saveSessionWorkspacePreferencesMock,
  saveWorkspace: saveWorkspaceMock,
}));

vi.mock('./layout/elkLayout', () => {
  const defaultDimensions = { width: 320, height: 160 };
  return {
    computeElkLayout: vi.fn(async () => new Map<string, { x: number; y: number }>()),
    getNodeDimensions: vi.fn(() => defaultDimensions),
  };
});

vi.mock('./layout/incrementalPlacement', () => ({
  buildLocalLayoutNeighborhood: vi.fn(() => ({
    nodes: [],
    edges: [],
    fixedPositions: new Map(),
  })),
  collectMeasuredNodeSizes: vi.fn(() => new Map()),
  createLocalNodePlacements: vi.fn(() => new Map()),
  isEligibleForElkRefinement: vi.fn(() => false),
  resolveNodeCollisions: vi.fn(
    ({ initialPositions }: { initialPositions: Map<string, { x: number; y: number }> }) =>
      initialPositions,
  ),
}));

vi.mock('./components/GraphAppearancePanel', () => ({
  default: () => null,
}));

vi.mock('./components/IngestPoller', () => ({
  default: () => null,
}));

vi.mock('./components/FilterPanel', async () => {
  const actual = await vi.importActual<typeof import('./components/FilterPanel')>('./components/FilterPanel');
  return {
    ...actual,
    default: () => null,
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

const SESSION_ID = 'sess-restore-workflow';
const ROOT_SELECTOR: AssetSelector = {
  mode: 'asset',
  chain: 'ethereum',
  chain_asset_id: '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
  asset_symbol: 'USDC',
};

const ROOT_NODE: InvestigationNode = {
  node_id: 'ethereum:address:0xaaa',
  node_type: 'address',
  branch_id: 'branch-1',
  path_id: 'path-1',
  lineage_id: 'lineage-root',
  depth: 0,
  chain: 'ethereum',
  display_label: '0xaaa',
  expandable_directions: ['next', 'prev', 'neighbors'],
  address_data: {
    address: '0xaaa',
    chain: 'ethereum',
  },
  is_seed: true,
};

const RESTORE_CANDIDATE: RecentSessionSummary = {
  session_id: SESSION_ID,
  seed_address: '0xaaa',
  seed_chain: 'ethereum',
  snapshot_saved_at: '2026-04-13T10:00:00Z',
};

function makeWorkspaceSnapshot(revision = 8): WorkspaceSnapshotV1 {
  return {
    schema_version: 1,
    revision,
    sessionId: SESSION_ID,
    nodes: [ROOT_NODE],
    edges: [],
    positions: {
      [ROOT_NODE.node_id]: { x: 12, y: 24 },
    },
    branches: [
      {
        branchId: 'branch-1',
        color: '#3b82f6',
        seedNodeId: ROOT_NODE.node_id,
        minDepth: 0,
        maxDepth: 0,
        nodeCount: 1,
      },
    ],
    nodeAssetScopes: {
      [ROOT_NODE.node_id]: [ROOT_SELECTOR],
    },
    workspacePreferences: {
      selectedAssets: [],
      pinnedAssetKeys: [],
      assetCatalogScope: 'session',
    },
  };
}

function makeSessionResponse(revision = 8): InvestigationSessionResponse {
  const workspace = makeWorkspaceSnapshot(revision);
  return {
    session_id: SESSION_ID,
    workspace,
    restore_state: 'full',
    nodes: workspace.nodes,
    edges: workspace.edges,
    branch_map: {
      'branch-1': workspace.branches?.[0]!,
    },
    snapshot_saved_at: '2026-04-13T10:00:00Z',
  };
}

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
    added_nodes: [],
    added_edges: [],
    updated_nodes: [],
    removed_node_ids: [],
    layout_hints: { suggested_layout: 'layered' },
    chain_context: { primary_chain: 'ethereum', chains_present: ['ethereum'] },
    empty_state: {
      reason: 'no_results',
      message: 'No indexed activity in the current dataset.',
      chain: 'ethereum',
      operation_type: request.operation_type,
    },
  };
}

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function waitFor<T>(assertion: () => T): Promise<T> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      return assertion();
    } catch (error) {
      lastError = error;
      await flush();
    }
  }
  throw lastError;
}

function findButton(text: string): HTMLButtonElement | null {
  return (
    Array.from(document.querySelectorAll('button')).find(
      (candidate) => candidate.textContent?.trim() === text,
    ) as HTMLButtonElement | undefined
  ) ?? null;
}

function getButton(text: string): HTMLButtonElement {
  const button = findButton(text);
  if (!button) {
    throw new Error(`Unable to find button "${text}"`);
  }
  return button;
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

function findByTestId(container: HTMLElement, testId: string): HTMLElement {
  const element = container.querySelector(`[data-testid="${testId}"]`);
  if (!element) {
    throw new Error(`Unable to find element with test id ${testId}`);
  }
  return element as HTMLElement;
}

function getLabeledInput(text: string, inputType: 'radio' | 'checkbox'): HTMLInputElement {
  const label = Array.from(document.querySelectorAll('label')).find(
    (candidate) => candidate.textContent?.trim() === text,
  );
  const input = label?.querySelector(`input[type="${inputType}"]`) as HTMLInputElement | null;
  if (!input) {
    throw new Error(`Unable to find ${inputType} labeled "${text}"`);
  }
  return input;
}

async function clickElement(element: HTMLElement): Promise<void> {
  await act(async () => {
    element.click();
    await Promise.resolve();
  });
}

async function clickButton(text: string): Promise<void> {
  await clickElement(getButton(text));
}

async function advanceAutosave(ms = 350): Promise<void> {
  await act(async () => {
    vi.advanceTimersByTime(ms);
    await Promise.resolve();
  });
}

describe('App restore workflow integration', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    createSessionMock.mockReset();
    expandNodeMock.mockReset();
    getAssetOptionsMock.mockReset();
    getRecentSessionsMock.mockReset();
    getSessionMock.mockReset();
    isAuthenticatedMock.mockReset();
    redirectToLoginMock.mockReset();
    saveSessionSnapshotMock.mockReset();
    clearSavedWorkspaceMock.mockReset();
    saveSessionWorkspacePreferencesMock.mockReset();
    saveWorkspaceMock.mockReset();

    isAuthenticatedMock.mockReturnValue(true);
    getRecentSessionsMock.mockResolvedValue({ items: [RESTORE_CANDIDATE] });
    getSessionMock.mockResolvedValue(makeSessionResponse());
    getAssetOptionsMock.mockResolvedValue({
      session_id: SESSION_ID,
      seed_node_id: ROOT_NODE.node_id,
      seed_lineage_id: ROOT_NODE.lineage_id,
      options: [
        { mode: 'all', chain: 'ethereum', display_label: 'All assets' },
        { ...ROOT_SELECTOR, display_label: 'USDC · Ethereum' },
      ],
    });
    expandNodeMock.mockImplementation(async (_sessionId: string, request: ExpandRequest) => (
      makeEmptyExpansion(request)
    ));
    saveWorkspaceMock.mockResolvedValue(undefined);

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (input === '/health') {
        return new Response(
          JSON.stringify({ auth_disabled: true }),
          {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          },
        );
      }
      throw new Error(`Unexpected fetch request: ${String(input)}`);
    });

    container = document.createElement('div');
    document.body.innerHTML = '';
    document.body.appendChild(container);
    root = createRoot(container);
    useGraphStore.getState().reset();
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    document.body.innerHTML = '';
    useGraphStore.getState().reset();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('restores backend asset scope and reuses it for quick expand and preview', async () => {
    await act(async () => {
      root.render(React.createElement(App));
    });

    await waitFor(() => {
      expect(getRecentSessionsMock).toHaveBeenCalledWith(1);
      expect(findButton('Restore Saved Workspace')).not.toBeNull();
    });

    await clickButton('Restore Saved Workspace');

    await waitFor(() => {
      expect(getSessionMock).toHaveBeenCalledWith(SESSION_ID);
      expect(findByTestId(container, `rf-node-${ROOT_NODE.node_id}`)).toBeTruthy();
    });

    expect(saveSessionWorkspacePreferencesMock).toHaveBeenCalledWith(
      SESSION_ID,
      makeWorkspaceSnapshot().workspacePreferences,
    );

    await clickElement(
      getButtonByTextWithin(
        findByTestId(container, `rf-node-${ROOT_NODE.node_id}`),
        'Next →',
      ),
    );

    await waitFor(() => {
      expect(expandNodeMock).toHaveBeenCalledWith(
        SESSION_ID,
        expect.objectContaining({
          operation_type: 'expand_next',
          seed_node_id: ROOT_NODE.node_id,
          options: expect.objectContaining({
            asset_selectors: [ROOT_SELECTOR],
          }),
        }),
      );
    });

    expandNodeMock.mockClear();

    await clickElement(findByTestId(container, `rf-node-${ROOT_NODE.node_id}`));

    await waitFor(() => {
      expect(getAssetOptionsMock).toHaveBeenCalledWith(SESSION_ID, {
        seed_node_id: ROOT_NODE.node_id,
        seed_lineage_id: ROOT_NODE.lineage_id,
      });
      expect(getLabeledInput('Specific assets', 'radio').checked).toBe(true);
      expect(getLabeledInput('USDC · Ethereum', 'checkbox').checked).toBe(true);
    });

    await clickButton('Filter & Preview ▼');
    await clickButton('Preview next');

    await waitFor(() => {
      expect(expandNodeMock).toHaveBeenCalledWith(
        SESSION_ID,
        expect.objectContaining({
          operation_type: 'expand_next',
          seed_node_id: ROOT_NODE.node_id,
          options: expect.objectContaining({
            asset_selectors: [ROOT_SELECTOR],
            max_results: 25,
          }),
        }),
      );
    });
  });

  it('restores the backend revision, advances it once, then pauses on 409 while local hints continue', async () => {
    vi.useFakeTimers();

    saveSessionSnapshotMock
      .mockResolvedValueOnce({
        snapshot_id: 'snap-1',
        saved_at: '2026-04-13T10:05:00Z',
        revision: 9,
      })
      .mockRejectedValueOnce(
        new MockApiError(409, 'API 409: stale workspace snapshot revision'),
      );

    await act(async () => {
      root.render(React.createElement(App));
    });

    await waitFor(() => {
      expect(findButton('Restore Saved Workspace')).not.toBeNull();
    });

    await clickButton('Restore Saved Workspace');

    await waitFor(() => {
      expect(findByTestId(container, `rf-node-${ROOT_NODE.node_id}`)).toBeTruthy();
    });

    await advanceAutosave();
    expect(saveSessionSnapshotMock).not.toHaveBeenCalled();

    await act(async () => {
      useGraphStore.getState().setNodeHidden(ROOT_NODE.node_id, true);
    });

    await advanceAutosave();
    await flush();

    expect(saveSessionSnapshotMock).toHaveBeenCalledTimes(1);
    expect(saveSessionSnapshotMock).toHaveBeenNthCalledWith(
      1,
      SESSION_ID,
      expect.objectContaining({
        revision: 8,
        sessionId: SESSION_ID,
        nodeAssetScopes: {
          [ROOT_NODE.node_id]: [ROOT_SELECTOR],
        },
      }),
    );

    await act(async () => {
      useGraphStore.getState().setNodeHidden(ROOT_NODE.node_id, false);
    });

    await advanceAutosave();
    await flush();

    expect(saveSessionSnapshotMock).toHaveBeenCalledTimes(2);
    expect(saveSessionSnapshotMock.mock.calls[1]?.[1]).toEqual(
      expect.objectContaining({
        revision: 9,
        sessionId: SESSION_ID,
      }),
    );
    expect(container.textContent).toContain('Autosave paused');
    expect(container.textContent).toContain('stale saved workspace revision');

    const localHintCallsAfterConflict = saveWorkspaceMock.mock.calls.length;

    await act(async () => {
      useGraphStore.getState().setNodeHidden(ROOT_NODE.node_id, true);
    });

    await advanceAutosave();
    await flush();

    expect(saveSessionSnapshotMock).toHaveBeenCalledTimes(2);
    expect(saveWorkspaceMock.mock.calls.length).toBeGreaterThan(localHintCallsAfterConflict);
  });
});
