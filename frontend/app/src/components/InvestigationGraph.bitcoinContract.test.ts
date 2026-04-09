// @vitest-environment jsdom

import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ExpandRequest, ExpansionResponseV2, InvestigationNode } from '../types/graph';
import { useGraphStore } from '../store/graphStore';
import InvestigationGraph from './InvestigationGraph';

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
  }: {
    nodes?: Array<{ id: string; data?: { display_label?: string } }>;
    edges?: Array<{ id: string }>;
    onNodeClick?: (event: MouseEvent, node: { id: string }) => void;
    onEdgeClick?: (event: MouseEvent, edge: { id: string }) => void;
    onPaneClick?: () => void;
    onInit?: (instance: { fitView: () => void; getNodes: () => unknown[] }) => void;
    children?: React.ReactNode;
  }) => {
    react.useEffect(() => {
      onInit?.({
        fitView: () => undefined,
        getNodes: () => nodes,
      });
    }, [nodes, onInit]);

    return react.createElement(
      'div',
      {
        'data-testid': 'react-flow',
        onClick: () => onPaneClick?.(),
      },
      [
        react.createElement(
          'div',
          { key: 'nodes', 'data-testid': 'rf-nodes' },
          nodes.map((node) => react.createElement(
            'button',
            {
              key: node.id,
              type: 'button',
              'data-nodeid': node.id,
              onClick: (event: MouseEvent) => {
                event.stopPropagation();
                onNodeClick?.(event, node);
              },
            },
            node.data?.display_label ?? node.id,
          )),
        ),
        react.createElement(
          'div',
          { key: 'edges', hidden: true },
          edges.map((edge) => react.createElement(
            'button',
            {
              key: edge.id,
              type: 'button',
              onClick: (event: MouseEvent) => {
                event.stopPropagation();
                onEdgeClick?.(event, edge);
              },
            },
            edge.id,
          )),
        ),
        children,
      ],
    );
  };

  return {
    ReactFlow,
    Background: () => null,
    Controls: () => null,
    MiniMap: () => null,
    BackgroundVariant: { Dots: 'dots' },
    useNodesState: (initialNodes: unknown[]) => {
      const [nodes, setNodes] = react.useState(initialNodes);
      return [nodes, setNodes, () => undefined] as const;
    },
    useEdgesState: (initialEdges: unknown[]) => {
      const [edges, setEdges] = react.useState(initialEdges);
      return [edges, setEdges, () => undefined] as const;
    },
  };
});

function makeBitcoinSeedNode(): InvestigationNode {
  return {
    node_id: 'bitcoin:address:bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh',
    lineage_id: 'lineage-btc-seed',
    node_type: 'address',
    branch_id: 'branch-btc-seed',
    path_id: 'path-btc-seed',
    depth: 0,
    display_label: 'bc1qxy2kgdyg...x0wlh',
    chain: 'bitcoin',
    is_seed: true,
    expandable_directions: ['next'],
    address_data: {
      address: 'bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh',
      chain: 'bitcoin',
      address_type: 'unknown',
    },
  };
}

function findButton(container: HTMLElement, label: string): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll('button')).find(
    (candidate) => candidate.textContent?.trim() === label,
  );
  expect(button).toBeTruthy();
  return button as HTMLButtonElement;
}

async function click(element: HTMLElement): Promise<void> {
  await act(async () => {
    element.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await Promise.resolve();
  });
}

describe('InvestigationGraph bitcoin asset UI contract', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);

    useGraphStore.getState().reset();
    useGraphStore.getState().initSession('sess-bitcoin', makeBitcoinSeedNode());

    expandNodeMock.mockReset();
    getAssetOptionsMock.mockReset();
    saveWorkspaceMock.mockReset();
    expandNodeMock.mockResolvedValue({
      session_id: 'sess-bitcoin',
      operation_id: 'op-expand-btc',
      operation_type: 'expand_next',
      nodes: [],
      edges: [],
    } as ExpansionResponseV2);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
      await Promise.resolve();
    });
    container.remove();
    useGraphStore.getState().reset();
  });

  it('skips bitcoin asset lookup, hides the selector, and still expands cleanly', async () => {
    await act(async () => {
      root.render(
        React.createElement(InvestigationGraph, {
          sessionId: 'sess-bitcoin',
          onStartNewInvestigation: () => undefined,
        }),
      );
      await Promise.resolve();
    });

    await click(findButton(container, 'bc1qxy2kgdyg...x0wlh'));

    expect(getAssetOptionsMock).not.toHaveBeenCalled();
    expect(container.textContent).not.toContain('Asset scope');

    await click(findButton(container, 'Expand next'));

    expect(getAssetOptionsMock).not.toHaveBeenCalled();
    expect(expandNodeMock).toHaveBeenCalledTimes(1);
    expect(expandNodeMock).toHaveBeenCalledWith('sess-bitcoin', {
      seed_node_id: 'bitcoin:address:bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh',
      seed_lineage_id: 'lineage-btc-seed',
      operation_type: 'expand_next',
      options: undefined,
    });
  });
});
