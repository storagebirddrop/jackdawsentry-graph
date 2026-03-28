/**
 * InvestigationGraph — the main React Flow canvas for the investigation view.
 *
 * Responsibilities:
 * - Renders the graph from the Zustand store.
 * - Triggers ELK layout after delta updates.
 * - Handles node expand clicks (dispatches to API, then applies delta).
 * - Shows overload warning at NODE_OVERLOAD_THRESHOLD.
 * - Hosts the filter panel (client-side node/edge hiding).
 * - Keeps the active inspector's bridge-hop status fresh while pending.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { IngestPendingContext } from '../context/IngestPendingContext';
import IngestPoller from './IngestPoller';
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node,
  type NodeChange,
  type Edge,
  type EdgeMouseHandler,
  type NodeMouseHandler,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { useGraphStore, type BranchMeta } from '../store/graphStore';
import { expandNode, getSessionAssets, saveSessionSnapshot } from '../api/client';
import { computeElkLayout } from '../layout/elkLayout';
import type {
  AssetCatalogItem,
  BridgeHopData,
  ExpandRequest,
  InvestigationEdge,
  InvestigationNode,
  WorkspaceSnapshotV1,
} from '../types/graph';
import { assetSelectionKeysForEdge, preferredAssetSelectionKeyForEdge } from '../types/graph';

import AddressNode from './nodes/AddressNode';
import EntityNode from './nodes/EntityNode';
import BridgeHopNode from './nodes/BridgeHopNode';
import ClusterSummaryNode from './nodes/ClusterSummaryNode';
import UTXONode from './nodes/UTXONode';
import SolanaInstructionNode from './nodes/SolanaInstructionNode';
import SwapEventNode from './nodes/SwapEventNode';
import LightningChannelOpenNode from './nodes/LightningChannelOpenNode';
import LightningChannelCloseNode from './nodes/LightningChannelCloseNode';
import BtcSidechainPegNode from './nodes/BtcSidechainPegNode';
import AtomicSwapNode from './nodes/AtomicSwapNode';
import FilterPanel, {
  type AssetCatalogScopeMode,
  type FilterState,
  DEFAULT_ASSET_CATALOG_SCOPE,
  DEFAULT_FILTERS,
} from './FilterPanel';
import GraphAppearancePanel from './GraphAppearancePanel';
import GraphInspectorPanel, { type PathStory } from './GraphInspectorPanel';
import InvestigationEdgeComponent from './edges/InvestigationEdge';
import {
  DEFAULT_GRAPH_APPEARANCE,
  type GraphAppearanceState,
} from './graphAppearance';
import {
  extractSnapshotWorkspacePreferences,
  loadSessionWorkspacePreferences,
  saveSessionWorkspacePreferences,
} from '../workspacePersistence';
import { useBridgeHopPoller } from '../hooks/useBridgeHopPoller';
import {
  bridgeProtocolLabel,
  bridgeRouteLabel,
  getBridgeProtocolColor,
  isNodeVisibleInView,
  semanticMetaForNode,
} from './graphVisuals';

const NODE_TYPES = {
  address: AddressNode,
  entity: EntityNode,
  bridge_hop: BridgeHopNode,
  cluster_summary: ClusterSummaryNode,
  utxo: UTXONode,
  swap_event: SwapEventNode,
  lightning_channel_open: LightningChannelOpenNode,
  lightning_channel_close: LightningChannelCloseNode,
  btc_sidechain_peg_in: BtcSidechainPegNode,
  btc_sidechain_peg_out: BtcSidechainPegNode,
  atomic_swap: AtomicSwapNode,
  service: EntityNode,
  solana_instruction: SolanaInstructionNode,
};

const EDGE_TYPES = {
  investigation: InvestigationEdgeComponent,
};

const NODE_OVERLOAD_THRESHOLD = 500;

type FitViewHandle = {
  fitView: (options?: {
    duration?: number;
    padding?: number;
    includeHiddenNodes?: boolean;
    maxZoom?: number;
    minZoom?: number;
  }) => void;
};

interface Props {
  sessionId: string;
  initialWorkspaceRevision: number;
  initialSavedAt: string | null;
  initialRestoreNotice: { tone: 'info' | 'error'; message: string } | null;
  onStartNewInvestigation: () => void;
}

interface SessionBriefing {
  title: string;
  headline: string;
  markdown: string;
}

/** Apply filter state to raw nodes/edges, returning the visible subset. */
function applyFilters(
  nodes: Node[],
  edges: Edge[],
  filters: FilterState,
  appearance: GraphAppearanceState,
  branchSelection: { activeBranchIds: string[]; rootBranchId: string | null },
): { nodes: Node[]; edges: Edge[] } {
  let visibleNodes = nodes.filter((node) => {
    const data = node.data as unknown as InvestigationNode;
    return !data.is_hidden && isNodeVisibleInView(data, appearance.viewMode);
  });
  let visibleEdges = edges;

  let visibleIds = new Set(visibleNodes.map((n) => n.id));
  visibleEdges = visibleEdges.filter(
    (e) => visibleIds.has(e.source) && visibleIds.has(e.target),
  );

  if (filters.chainFilter.length > 0) {
    visibleNodes = visibleNodes.filter((n) => {
      const chain = (n.data as Record<string, unknown>)?.chain as string | undefined;
      return !chain || filters.chainFilter.includes(chain);
    });
    visibleIds = new Set(visibleNodes.map((n) => n.id));
    visibleEdges = visibleEdges.filter(
      (e) => visibleIds.has(e.source) && visibleIds.has(e.target),
    );
  }

  if (filters.maxDepth < 20) {
    visibleNodes = visibleNodes.filter((n) => {
      const depth = (n.data as Record<string, unknown>)?.depth as number | undefined;
      return depth === undefined || depth <= filters.maxDepth;
    });
    visibleIds = new Set(visibleNodes.map((n) => n.id));
    visibleEdges = visibleEdges.filter(
      (e) => visibleIds.has(e.source) && visibleIds.has(e.target),
    );
  }

  const minFiat = filters.minFiatValue;
  if (minFiat !== null && minFiat > 0) {
    visibleEdges = visibleEdges.filter((e) => {
      const val = (e.data as Record<string, unknown>)?.fiat_value_usd as number | undefined;
      return val === undefined || val >= minFiat;
    });
  }

  if (filters.selectedAssets.length > 0) {
    const selectedAssets = new Set(filters.selectedAssets.map((asset) => asset.toLowerCase()));
    visibleEdges = visibleEdges.filter((e) => {
      const edgeData = (e.data ?? {}) as unknown as InvestigationEdge;
      return assetSelectionKeysForEdge(edgeData).some((key) => selectedAssets.has(key));
    });
  }

  if (filters.assetFilter.trim()) {
    const q = filters.assetFilter.trim().toLowerCase();
    visibleEdges = visibleEdges.filter((e) => {
      const sym = (e.data as Record<string, unknown>)?.asset_symbol as string | undefined;
      return !sym || sym.toLowerCase().includes(q);
    });
  }

  const hasBridgeFilters =
    filters.bridgeProtocols.length > 0 ||
    filters.bridgeStatuses.length > 0 ||
    Boolean(filters.bridgeRoute);

  if (hasBridgeFilters) {
    const matchingBridgeNodeIds = new Set(
      visibleNodes
        .filter((node) => {
          const data = node.data as unknown as InvestigationNode;
          if (data.node_type !== 'bridge_hop') return false;

          const hop = (data.bridge_hop_data ?? data.node_data) as BridgeHopData | undefined;
          if (!hop) return false;

          const protocolId = hop.protocol_id?.toLowerCase();
          const status = hop.status?.toLowerCase() as FilterState['bridgeStatuses'][number] | undefined;
          const route = bridgeRouteLabel({
            source_chain: hop.source_chain,
            destination_chain: hop.destination_chain,
          }).toLowerCase();

          const protocolMatch =
            filters.bridgeProtocols.length === 0 ||
            (protocolId !== undefined && filters.bridgeProtocols.includes(protocolId));
          const statusMatch =
            filters.bridgeStatuses.length === 0 ||
            (status !== undefined && filters.bridgeStatuses.includes(status));
          const routeMatch =
            !filters.bridgeRoute || route === filters.bridgeRoute.toLowerCase();

          return protocolMatch && statusMatch && routeMatch;
        })
        .map((node) => node.id),
    );

    visibleEdges = visibleEdges.filter(
      (edge) =>
        matchingBridgeNodeIds.has(edge.source) ||
        matchingBridgeNodeIds.has(edge.target),
    );

    const contextualNodeIds = new Set<string>(matchingBridgeNodeIds);
    for (const edge of visibleEdges) {
      contextualNodeIds.add(edge.source);
      contextualNodeIds.add(edge.target);
    }

    visibleNodes = visibleNodes.filter((node) => contextualNodeIds.has(node.id));
  }

  if (branchSelection.activeBranchIds.length > 0) {
    const allowedBranches = new Set(
      [
        ...branchSelection.activeBranchIds,
        branchSelection.rootBranchId,
      ].filter((value): value is string => Boolean(value)),
    );

    visibleNodes = visibleNodes.filter((node) => {
      const data = node.data as unknown as InvestigationNode;
      return allowedBranches.has(data.branch_id);
    });
    visibleEdges = visibleEdges.filter((edge) => {
      const data = edge.data as Record<string, unknown> | undefined;
      const branchId = data?.branch_id as string | undefined;
      return branchId ? allowedBranches.has(branchId) : true;
    });
  }

  return { nodes: visibleNodes, edges: visibleEdges };
}

export default function InvestigationGraph({
  sessionId,
  initialWorkspaceRevision,
  initialSavedAt,
  initialRestoreNotice,
  onStartNewInvestigation,
}: Props) {
  const {
    rfNodes,
    rfEdges,
    setRfPositions,
    syncRfPositions,
    applyExpansionDelta,
    setExpandingNode,
    setNodeHidden,
    restoreAllHiddenNodes,
    expandingNodeIds,
    exportSnapshot,
    importSnapshot,
    branchMap,
    updateBridgeHopStatus,
  } = useGraphStore();

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(rfNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(rfEdges);

  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  const [filterVisible, setFilterVisible] = useState(false);
  const [appearance, setAppearance] = useState<GraphAppearanceState>(DEFAULT_GRAPH_APPEARANCE);
  const [appearanceVisible, setAppearanceVisible] = useState(false);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(false);
  const [briefingVisible, setBriefingVisible] = useState(false);
  const [notice, setNotice] = useState<{
    tone: 'info' | 'error';
    message: string;
    autoDismiss: boolean;
  } | null>(null);
  const [assetCatalog, setAssetCatalog] = useState<AssetCatalogItem[]>([]);
  const [assetCatalogScope, setAssetCatalogScope] = useState<AssetCatalogScopeMode>(DEFAULT_ASSET_CATALOG_SCOPE);
  const [pinnedAssetKeys, setPinnedAssetKeys] = useState<string[]>([]);
  const [sessionSaveStatus, setSessionSaveStatus] = useState<'idle' | 'dirty' | 'saving' | 'saved' | 'save_failed'>(
    initialSavedAt ? 'saved' : 'idle',
  );
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(initialSavedAt);
  const [bridgeRouteHistory, setBridgeRouteHistory] = useState<string[]>([]);
  const [activeBranchIds, setActiveBranchIds] = useState<string[]>([]);
  const [branchHistory, setBranchHistory] = useState<string[]>([]);
  const [pinnedPathIds, setPinnedPathIds] = useState<string[]>([]);
  const [activeSemanticKey, setActiveSemanticKey] = useState<string | null>(null);

  // Tracks nodes whose expansion returned ingest_pending=true.
  // Key: node_id. Value: { address, chain } needed for status polling.
  const [ingestPendingMap, setIngestPendingMap] = useState<
    Map<string, { address: string; chain: string }>
  >(new Map());
  // Stores the pending retry payload so handleIngestComplete can re-expand.
  const ingestRetryRef = useRef<
    Map<string, { node: Pick<InvestigationNode, 'node_id' | 'lineage_id'>; operation: ExpandRequest['operation_type'] }>
  >(new Map());
  const reactFlowRef = useRef<FitViewHandle | null>(null);
  const fitViewTimerRef = useRef<number | null>(null);
  const autosaveTimerRef = useRef<number | null>(null);
  const autosaveRequestIdRef = useRef(0);
  const autosaveRevisionRef = useRef(initialWorkspaceRevision);
  const lastPersistedSnapshotRef = useRef<string | null>(null);
  const didSeedAutosaveBaselineRef = useRef(false);
  const didHydrateManualLayoutRef = useRef(false);
  const didHydrateSessionPrefsRef = useRef(false);
  const lastSavedPrefsRef = useRef<{
    selectedAssets: string[];
    pinnedAssetKeys: string[];
    assetCatalogScope: string;
  } | null>(null);

  const showNotice = useCallback((
    message: string,
    tone: 'info' | 'error' = 'info',
    options?: { autoDismiss?: boolean },
  ) => {
    setNotice({ tone, message, autoDismiss: options?.autoDismiss ?? true });
  }, []);

  useEffect(() => {
    autosaveRevisionRef.current = initialWorkspaceRevision;
    didSeedAutosaveBaselineRef.current = false;
    lastPersistedSnapshotRef.current = null;
    setSessionSaveStatus(initialSavedAt ? 'saved' : 'idle');
    setLastSavedAt(initialSavedAt);
  }, [initialSavedAt, initialWorkspaceRevision, sessionId]);

  useEffect(() => {
    if (!initialRestoreNotice) return;
    showNotice(initialRestoreNotice.message, initialRestoreNotice.tone);
  }, [initialRestoreNotice, showNotice]);

  const scheduleFitView = useCallback((duration = 260) => {
    if (typeof window === 'undefined') return;

    if (fitViewTimerRef.current !== null) {
      window.clearTimeout(fitViewTimerRef.current);
    }

    fitViewTimerRef.current = window.setTimeout(() => {
      fitViewTimerRef.current = null;
      reactFlowRef.current?.fitView({
        duration,
        padding: 0.2,
        includeHiddenNodes: false,
        maxZoom: 1.15,
        minZoom: 0.12,
      });
    }, 60);
  }, []);

  useEffect(() => () => {
    if (fitViewTimerRef.current !== null) {
      window.clearTimeout(fitViewTimerRef.current);
      fitViewTimerRef.current = null;
    }
    if (autosaveTimerRef.current !== null) {
      window.clearTimeout(autosaveTimerRef.current);
      autosaveTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    didHydrateSessionPrefsRef.current = false;
    const savedPreferences = loadSessionWorkspacePreferences(sessionId);
    if (savedPreferences) {
      setFilters((current) => ({
        ...current,
        selectedAssets: savedPreferences.selectedAssets,
      }));
      setPinnedAssetKeys(savedPreferences.pinnedAssetKeys);
      setAssetCatalogScope(savedPreferences.assetCatalogScope);
    } else {
      setFilters((current) => ({ ...current, selectedAssets: [] }));
      setPinnedAssetKeys([]);
      setAssetCatalogScope(DEFAULT_ASSET_CATALOG_SCOPE);
    }
    didHydrateSessionPrefsRef.current = true;
  }, [sessionId]);

  const branchEntries = useMemo(
    () =>
      Array.from(branchMap.values()).sort((a, b) => {
        if (a.minDepth === 0 && b.minDepth !== 0) return -1;
        if (b.minDepth === 0 && a.minDepth !== 0) return 1;
        return b.nodeCount - a.nodeCount;
      }),
    [branchMap],
  );

  const rootBranchId = useMemo(
    () => branchEntries.find((branch) => branch.minDepth === 0)?.branchId ?? branchEntries[0]?.branchId ?? null,
    [branchEntries],
  );

  const branchMetaById = useMemo(
    () => new Map(branchEntries.map((branch) => [branch.branchId, branch])),
    [branchEntries],
  );

  const pathStories = useMemo<PathStory[]>(() => {
    const storyMap = new Map<
      string,
      {
        pathId: string;
        lineageId: string;
        nodes: InvestigationNode[];
        branches: Set<string>;
        chains: Set<string>;
      }
    >();

    for (const node of rfNodes) {
      const data = node.data as unknown as InvestigationNode;
      const existing = storyMap.get(data.path_id) ?? {
        pathId: data.path_id,
        lineageId: data.lineage_id,
        nodes: [],
        branches: new Set<string>(),
        chains: new Set<string>(),
      };
      existing.nodes.push(data);
      existing.branches.add(data.branch_id);
      const chain = data.chain ?? data.address_data?.chain;
      if (chain) existing.chains.add(chain);
      storyMap.set(data.path_id, existing);
    }

    return Array.from(storyMap.values())
      .map((story) => {
        const orderedNodes = [...story.nodes].sort((a, b) => a.depth - b.depth);
        const firstNode = orderedNodes[0];
        const lastNode = orderedNodes[orderedNodes.length - 1];
        const primaryBranch = branchMetaById.get(firstNode.branch_id);
        const firstLabel = pathStoryNodeLabel(firstNode);
        const lastLabel = pathStoryNodeLabel(lastNode);

        return {
          pathId: story.pathId,
          lineageId: story.lineageId,
          title: firstLabel,
          summary: firstNode.node_id === lastNode.node_id
            ? firstLabel
            : `${firstLabel} -> ${lastLabel}`,
          nodeCount: orderedNodes.length,
          minDepth: firstNode.depth,
          maxDepth: lastNode.depth,
          branchCount: story.branches.size,
          chains: Array.from(story.chains),
          color: primaryBranch?.color ?? '#2563eb',
        };
      })
      .sort((a, b) => {
        if (b.nodeCount !== a.nodeCount) return b.nodeCount - a.nodeCount;
        return a.minDepth - b.minDepth;
      });
  }, [rfNodes, branchMetaById]);

  const pathStoryById = useMemo(
    () => new Map(pathStories.map((story) => [story.pathId, story])),
    [pathStories],
  );

  const bridgeFilterOptions = useMemo(() => {
    const protocols = new Set<string>();
    const routes = new Set<string>();

    for (const node of rfNodes) {
      const data = node.data as unknown as InvestigationNode;
      if (data.node_type !== 'bridge_hop') continue;

      const hop = (data.bridge_hop_data ?? data.node_data) as BridgeHopData | undefined;
      if (!hop) continue;

      if (hop.protocol_id) {
        protocols.add(hop.protocol_id.toLowerCase());
      }
      routes.add(
        bridgeRouteLabel({
          source_chain: hop.source_chain,
          destination_chain: hop.destination_chain,
        }),
      );
    }

    return {
      protocols: [...protocols].sort(),
      routes: [...routes].sort(),
    };
  }, [rfNodes]);

  const catalogChains = useMemo(() => {
    const chains = new Set<string>();
    for (const node of rfNodes) {
      const data = node.data as unknown as InvestigationNode;
      const chain = data.chain ?? data.address_data?.chain;
      if (chain) chains.add(chain.toLowerCase());
    }
    return [...chains].sort((left, right) => left.localeCompare(right));
  }, [rfNodes]);

  const fallbackAssets = useMemo<AssetCatalogItem[]>(() => {
    const assets = new Map<string, AssetCatalogItem>();
    for (const edge of rfEdges) {
      const data = (edge.data ?? {}) as unknown as InvestigationEdge;
      const symbol = data.asset_symbol?.trim();
      if (!symbol) continue;
      const assetKey = preferredAssetSelectionKeyForEdge(data) ?? symbol;
      const existing = assets.get(assetKey);
      if (existing) {
        existing.observed_transfer_count += 1;
        continue;
      }
      assets.set(assetKey, {
        asset_key: assetKey,
        symbol,
        canonical_asset_id: data.canonical_asset_id,
        canonical_symbol: undefined,
        identity_status: 'unknown',
        variant_kind: 'unknown',
        blockchains: data.asset_chain ? [data.asset_chain] : [],
        token_standards: [],
        observed_transfer_count: 1,
        sample_asset_address: data.asset_address,
        is_native: false,
      });
    }
    return [...assets.values()].sort((left, right) => left.symbol.localeCompare(right.symbol));
  }, [rfEdges]);

  const sessionAvailableAssets = assetCatalog.length > 0 ? assetCatalog : fallbackAssets;

  const lensScopedGraphForAssets = useMemo(
    () => applyFilters(
      rfNodes,
      rfEdges,
      {
        ...filters,
        selectedAssets: [],
        assetFilter: '',
      },
      appearance,
      { activeBranchIds, rootBranchId },
    ),
    [rfNodes, rfEdges, filters, appearance, activeBranchIds, rootBranchId],
  );

  const visibleLensAssetKeys = useMemo(() => {
    const keys = new Set<string>();
    for (const edge of lensScopedGraphForAssets.edges) {
      const data = (edge.data ?? {}) as unknown as InvestigationEdge;
      for (const key of assetSelectionKeysForEdge(data)) {
        keys.add(key);
      }
    }
    return keys;
  }, [lensScopedGraphForAssets.edges]);

  const visibleLensAssets = useMemo(
    () => sessionAvailableAssets.filter((asset) => visibleLensAssetKeys.has(asset.asset_key)),
    [sessionAvailableAssets, visibleLensAssetKeys],
  );

  const availableAssets = useMemo(() => {
    if (assetCatalogScope !== 'visible') {
      return sessionAvailableAssets;
    }
    const selectedAssetSet = new Set(filters.selectedAssets);
    const pinnedAssetSet = new Set(pinnedAssetKeys);
    return sessionAvailableAssets.filter((asset) => (
      visibleLensAssetKeys.has(asset.asset_key)
      || selectedAssetSet.has(asset.asset_key)
      || pinnedAssetSet.has(asset.asset_key)
    ));
  }, [assetCatalogScope, filters.selectedAssets, pinnedAssetKeys, sessionAvailableAssets, visibleLensAssetKeys]);

  const semanticLegend = useMemo(() => {
    const entries = new Map<
      string,
      { key: string; label: string; family: string; color: string; count: number }
    >();
    const families = new Map<string, { family: string; color: string; count: number }>();

    for (const node of nodes) {
      const meta = semanticMetaForNode(node.data as unknown as InvestigationNode);
      if (!meta) continue;

      const entry = entries.get(meta.key) ?? {
        key: meta.key,
        label: meta.label,
        family: meta.family,
        color: meta.color,
        count: 0,
      };
      entry.count += 1;
      entries.set(meta.key, entry);

      const family = families.get(meta.family) ?? {
        family: meta.family,
        color: meta.color,
        count: 0,
      };
      family.count += 1;
      families.set(meta.family, family);
    }

    return {
      entries: [...entries.values()].sort((a, b) => {
        if (b.count !== a.count) return b.count - a.count;
        return a.label.localeCompare(b.label);
      }),
      families: [...families.values()].sort((a, b) => b.count - a.count),
    };
  }, [nodes]);

  const hiddenNodeCount = useMemo(
    () =>
      rfNodes.reduce((count, node) => {
        const data = node.data as unknown as InvestigationNode;
        return count + (data.is_hidden ? 1 : 0);
      }, 0),
    [rfNodes],
  );

  // Keep local RF state in sync with store, applying current filters
  useEffect(() => {
    const { nodes: fn, edges: fe } = applyFilters(
      rfNodes,
      rfEdges,
      filters,
      appearance,
      { activeBranchIds, rootBranchId },
    );
    setNodes(fn);
    setEdges(fe);
  }, [rfNodes, rfEdges, filters, appearance, activeBranchIds, rootBranchId, setNodes, setEdges]);

  useEffect(() => {
    let cancelled = false;

    async function loadAssetCatalog() {
      try {
        const response = await getSessionAssets(sessionId, catalogChains);
        if (!cancelled) {
          setAssetCatalog(response.items ?? []);
        }
      } catch (error) {
        console.warn('Failed to load session asset catalog', error);
        if (!cancelled) {
          setAssetCatalog([]);
        }
      }
    }

    void loadAssetCatalog();

    return () => {
      cancelled = true;
    };
  }, [sessionId, catalogChains]);

  useEffect(() => {
    if (!didHydrateSessionPrefsRef.current) {
      return;
    }
    const next = { selectedAssets: filters.selectedAssets, pinnedAssetKeys, assetCatalogScope };
    const prev = lastSavedPrefsRef.current;
    if (
      prev !== null &&
      prev.assetCatalogScope === next.assetCatalogScope &&
      prev.selectedAssets.length === next.selectedAssets.length &&
      prev.selectedAssets.every((v, i) => v === next.selectedAssets[i]) &&
      prev.pinnedAssetKeys.length === next.pinnedAssetKeys.length &&
      prev.pinnedAssetKeys.every((v, i) => v === next.pinnedAssetKeys[i])
    ) {
      return;
    }
    lastSavedPrefsRef.current = next;
    saveSessionWorkspacePreferences(sessionId, next);
  }, [sessionId, filters.selectedAssets, pinnedAssetKeys, assetCatalogScope]);

  const currentSnapshotWorkspacePreferences = useMemo(() => ({
    selectedAssets: filters.selectedAssets,
    pinnedAssetKeys,
    assetCatalogScope,
  }), [filters.selectedAssets, pinnedAssetKeys, assetCatalogScope]);

  const sessionSaveLabel = useMemo(() => {
    if (sessionSaveStatus === 'saving') return 'Saving…';
    if (sessionSaveStatus === 'save_failed') return 'Save failed';
    if (sessionSaveStatus === 'dirty') return 'Unsaved changes';
    if (lastSavedAt) {
      return `Saved ${new Date(lastSavedAt).toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
      })}`;
    }
    return 'Not saved yet';
  }, [sessionSaveStatus, lastSavedAt]);

  useEffect(() => {
    if (
      typeof window === 'undefined'
      || !sessionId
      || rfNodes.length === 0
      || !didHydrateSessionPrefsRef.current
    ) {
      return;
    }

    const snapshotJson = exportSnapshot({
      workspacePreferences: currentSnapshotWorkspacePreferences,
    });

    if (!didSeedAutosaveBaselineRef.current) {
      didSeedAutosaveBaselineRef.current = true;
      lastPersistedSnapshotRef.current = snapshotJson;
      return;
    }

    if (snapshotJson === lastPersistedSnapshotRef.current) {
      return;
    }

    setSessionSaveStatus((current) => (current === 'saving' ? current : 'dirty'));

    if (autosaveTimerRef.current !== null) {
      window.clearTimeout(autosaveTimerRef.current);
    }

    autosaveTimerRef.current = window.setTimeout(() => {
      autosaveTimerRef.current = null;
      const requestId = ++autosaveRequestIdRef.current;

      let snapshotPayload: WorkspaceSnapshotV1;
      try {
        snapshotPayload = JSON.parse(snapshotJson) as WorkspaceSnapshotV1;
      } catch (error) {
        console.error('Failed to serialise workspace snapshot for autosave:', error);
        setSessionSaveStatus('save_failed');
        return;
      }

      const nextRevision = autosaveRevisionRef.current + 1;
      autosaveRevisionRef.current = nextRevision;
      snapshotPayload.revision = nextRevision;
      setSessionSaveStatus('saving');
      void saveSessionSnapshot(sessionId, snapshotPayload)
        .then((response) => {
          if (autosaveRequestIdRef.current !== requestId) return;
          autosaveRevisionRef.current = response.revision;
          lastPersistedSnapshotRef.current = snapshotJson;
          setSessionSaveStatus('saved');
          setLastSavedAt(response.saved_at);
        })
        .catch((error) => {
          if (autosaveRequestIdRef.current !== requestId) return;
          console.error('Failed to autosave session workspace:', error);
          if (error instanceof Error && error.message.includes('API 409')) {
            showNotice(
              'A newer workspace snapshot reached the server first. Your latest graph state remains unsaved until the next successful autosave.',
              'error',
            );
          }
          setSessionSaveStatus('save_failed');
        });
    }, 2000);

    return () => {
      if (autosaveTimerRef.current !== null) {
        window.clearTimeout(autosaveTimerRef.current);
        autosaveTimerRef.current = null;
      }
    };
  }, [
    sessionId,
    rfNodes,
    rfEdges,
    branchMap,
    exportSnapshot,
    currentSnapshotWorkspacePreferences,
    showNotice,
  ]);

  // Incremental ELK layout: only newly added nodes are placed freely.
  // Nodes that have already been laid out are passed to ELK as fixed-position
  // hints (enabling interactiveLayout mode) and their returned positions are
  // discarded — preserving the investigator's mental map across expansions.
  const layoutedNodeIds = useRef<Set<string>>(new Set());
  const layoutRef = useRef<number | null>(null);
  useEffect(() => {
    if (rfNodes.length === 0) {
      layoutedNodeIds.current.clear();
      didHydrateManualLayoutRef.current = false;
      return;
    }

    if (!didHydrateManualLayoutRef.current) {
      const hasSavedPositions =
        rfNodes.length > 1
        && rfNodes.some(
          (node) => Math.abs(node.position.x) > 1 || Math.abs(node.position.y) > 1,
        );
      if (hasSavedPositions) {
        layoutedNodeIds.current = new Set(rfNodes.map((node) => node.id));
        didHydrateManualLayoutRef.current = true;
        return;
      }
      didHydrateManualLayoutRef.current = true;
    }

    const newNodes = rfNodes.filter((n) => !layoutedNodeIds.current.has(n.id));
    if (newNodes.length === 0) return; // Nothing new to place.

    const currentLayout = Date.now();
    layoutRef.current = currentLayout;

    // Build fixed-position map for already-laid-out nodes so ELK treats them
    // as anchors when computing layer assignments for new nodes.
    const fixedPositions = new Map<string, { x: number; y: number }>();
    for (const n of rfNodes) {
      if (layoutedNodeIds.current.has(n.id)) {
        fixedPositions.set(n.id, n.position);
      }
    }

    // Snapshot the IDs being laid out in this pass before the async gap.
    const passingNewIds = new Set(newNodes.map((n) => n.id));

    computeElkLayout(rfNodes, rfEdges, fixedPositions)
      .then((positions) => {
        if (layoutRef.current !== currentLayout) return;
        // Apply ELK output only for nodes placed in this pass — existing
        // nodes retain their current positions regardless of what ELK returns.
        const deltaPositions = new Map<string, { x: number; y: number }>();
        for (const [id, pos] of positions) {
          if (passingNewIds.has(id)) deltaPositions.set(id, pos);
        }
        // Mark all nodes submitted to ELK in this pass as laid out
        // so a rapid second expansion doesn't re-layout same nodes.
        for (const nodeId of passingNewIds) layoutedNodeIds.current.add(nodeId);
        setRfPositions(deltaPositions);
      })
      .catch((error) => {
        console.error('ELK layout computation failed:', error);
      });
  }, [rfNodes, rfEdges, setRfPositions]);

  // Expand a node in a given direction
  const handleExpand = useCallback(
    async (
      node: Pick<InvestigationNode, 'node_id' | 'lineage_id'>,
      operation: ExpandRequest['operation_type'],
      txHashes?: string[],
    ) => {
      if (expandingNodeIds.has(node.node_id)) return;
      if (rfNodes.length >= NODE_OVERLOAD_THRESHOLD) {
        showNotice(
          `Graph already has ${rfNodes.length} nodes. Start a new investigation or reduce the current lens before expanding further.`,
        );
        return;
      }
      setExpandingNode(node.node_id, true);
      try {
        const nodeParts = node.node_id.split(':');
        const nodeChain = nodeParts[0]?.toUpperCase() ?? 'this';
        const expandOptions: NonNullable<ExpandRequest['options']> = {};
        if (txHashes && txHashes.length > 0) {
          expandOptions.tx_hashes = txHashes;
        }
        if (filters.selectedAssets.length > 0) {
          expandOptions.asset_filter = filters.selectedAssets;
        }
        const response = await expandNode(sessionId, {
          seed_node_id: node.node_id,
          seed_lineage_id: node.lineage_id,
          operation_type: operation,
          options: Object.keys(expandOptions).length > 0 ? expandOptions : undefined,
        });
        const deltaNodes = response.nodes ?? response.added_nodes ?? [];
        const deltaEdges = response.edges ?? response.added_edges ?? [];

        if (deltaNodes.length === 0 && deltaEdges.length === 0) {
          if (response.ingest_pending) {
            // Background ingest triggered — parse address/chain from node_id
            // format "{chain}:{type}:{identifier}" and start polling.
            const parts = node.node_id.split(':');
            const nodeChain = parts[0] ?? '';
            const nodeAddress = parts.slice(2).join(':');
            setIngestPendingMap((prev) => {
              const next = new Map(prev);
              next.set(node.node_id, { address: nodeAddress, chain: nodeChain });
              return next;
            });
            ingestRetryRef.current.set(node.node_id, { node, operation });
            showNotice(
              `Fetching ${expandOperationLabel(operation)} activity for this address — will retry automatically when data is ready.`,
            );
          } else {
            showNotice(
              response.empty_state?.message
                ?? `No indexed ${nodeChain} ${expandOperationLabel(operation)} activity was found for this node in the current graph dataset.`,
            );
          }
          return;
        }

        const existingNodeIds = new Set(rfNodes.map((existingNode) => existingNode.id));
        const existingEdgeIds = new Set(rfEdges.map((existingEdge) => existingEdge.id));
        const hasFreshDelta =
          deltaNodes.some((deltaNode) => !existingNodeIds.has(deltaNode.node_id))
          || deltaEdges.some((deltaEdge) => !existingEdgeIds.has(deltaEdge.edge_id));

        if (!hasFreshDelta) {
          showNotice(
            `This ${expandOperationLabel(operation)} path is already visible on the canvas.`,
          );
          return;
        }

        applyExpansionDelta(response);
      } catch (err) {
        console.error('Expand failed:', err);
        const message = err instanceof Error ? err.message : 'Unable to expand this node right now.';
        showNotice(message, 'error');
      } finally {
        setExpandingNode(node.node_id, false);
      }
    },
    [sessionId, rfNodes, rfEdges, expandingNodeIds, setExpandingNode, applyExpansionDelta, showNotice, filters.selectedAssets],
  );

  // Derived set consumed by IngestPendingContext and the enrichedNodes mapping.
  const ingestPendingNodeIds = useMemo(
    () => new Set(ingestPendingMap.keys()),
    [ingestPendingMap],
  );

  const handleIngestComplete = useCallback(
    (nodeId: string) => {
      // Remove from pending set.
      setIngestPendingMap((prev) => {
        const next = new Map(prev);
        next.delete(nodeId);
        return next;
      });
      // Retry the expansion so the newly-ingested data appears on the canvas.
      const retry = ingestRetryRef.current.get(nodeId);
      ingestRetryRef.current.delete(nodeId);
      if (retry) {
        void handleExpand(retry.node, retry.operation);
      }
    },
    [handleExpand],
  );

  const handleIngestTimeout = useCallback((nodeId: string) => {
    setIngestPendingMap((prev) => {
      const next = new Map(prev);
      next.delete(nodeId);
      return next;
    });
    ingestRetryRef.current.delete(nodeId);
    showNotice(
      'Background data fetch did not complete before the current timeout. Current results may be incomplete, and additional ingest may not be available in this runtime.',
      'error',
      { autoDismiss: false },
    );
  }, [showNotice]);

  const handleIngestUnavailable = useCallback((nodeId: string) => {
    setIngestPendingMap((prev) => {
      const next = new Map(prev);
      next.delete(nodeId);
      return next;
    });
    ingestRetryRef.current.delete(nodeId);
    showNotice(
      'Background data fetch is no longer queued for this address. Current results may be incomplete; try expanding the node again later.',
      'error',
    );
  }, [showNotice]);

  const handleNodeClick: NodeMouseHandler = useCallback((_evt, node) => {
    setSelectedNodeId(node.id);
    setSelectedEdgeId(null);
  }, []);

  const handleEdgeClick: EdgeMouseHandler = useCallback((_evt, edge) => {
    setSelectedEdgeId(edge.id);
    setSelectedNodeId(null);
  }, []);

  const handleNodesChange = useCallback((changes: NodeChange<Node>[]) => {
    onNodesChange(changes);

    const settledPositions = changes
      .filter(
        (change): change is NodeChange<Node> & { type: 'position'; position: { x: number; y: number } } =>
          change.type === 'position' && Boolean(change.position) && change.dragging !== true,
      )
      .map((change) => ({
        id: change.id,
        position: change.position,
      }));

    if (settledPositions.length > 0) {
      syncRfPositions(settledPositions);
    }
  }, [onNodesChange, syncRfPositions]);

  const handleNodeDragStop = useCallback((_event: unknown, node: Node) => {
    syncRfPositions([{ id: node.id, position: node.position }]);
  }, [syncRfPositions]);

  const handleHideNode = useCallback((nodeId: string) => {
    setNodeHidden(nodeId, true);
    if (selectedNodeId === nodeId) {
      setSelectedNodeId(null);
    }
    showNotice('Node removed from canvas. Use Restore removed to bring it back.');
  }, [selectedNodeId, setNodeHidden, showNotice]);

  const handleReframeGraph = useCallback(() => {
    scheduleFitView(180);
  }, [scheduleFitView]);

  const pinnedPathSet = useMemo(() => new Set(pinnedPathIds), [pinnedPathIds]);
  const visibleNodeSemanticById = useMemo(
    () =>
      new Map(
        nodes.map((node) => [
          node.id,
          semanticMetaForNode((node.data as unknown) as InvestigationNode)?.key ?? null,
        ]),
      ),
    [nodes],
  );
  const visibleNodePathById = useMemo(
    () =>
      new Map(
        nodes.map((node) => [node.id, ((node.data as unknown) as InvestigationNode).path_id]),
      ),
    [nodes],
  );

  // Inject expand handlers into node data
  const enrichedNodes = nodes.map((n) => {
    const invNode = n.data as unknown as InvestigationNode;
    const semanticMeta = semanticMetaForNode(invNode);
    const pinned = pinnedPathSet.has(invNode.path_id);
    const semanticMatch = !activeSemanticKey || semanticMeta?.key === activeSemanticKey;
    const dimmed = (pinnedPathIds.length > 0 && !pinned) || !semanticMatch;
    return {
      ...n,
      style: {
        ...(n.style ?? {}),
        opacity: dimmed ? 0.22 : 1,
        filter:
          pinned
            ? 'drop-shadow(0 0 0.35rem rgba(245,158,11,0.45))'
            : !semanticMatch && activeSemanticKey
              ? 'saturate(0.65)'
              : semanticMeta?.key === activeSemanticKey
                ? `drop-shadow(0 0 0.28rem ${semanticMeta.color}55)`
                : 'none',
        transition: 'opacity 120ms ease, filter 120ms ease',
      },
      data: {
        ...n.data,
        onExpandNext: () => handleExpand(invNode, 'expand_next'),
        onExpandPrev: () => handleExpand(invNode, 'expand_prev'),
        isExpanding: expandingNodeIds.has(n.id),
        isIngestPending: ingestPendingNodeIds.has(n.id),
        appearance,
        isPathPinned: pinned,
        hasPinnedPaths: pinnedPathIds.length > 0,
      },
    };
  });

  const enrichedEdges = edges.map((edge) => ({
    ...edge,
    style: (() => {
      const baseStyle = (edge.style ?? {}) as React.CSSProperties;
      const sourcePathId = visibleNodePathById.get(edge.source);
      const targetPathId = visibleNodePathById.get(edge.target);
      const sourceSemanticKey = visibleNodeSemanticById.get(edge.source);
      const targetSemanticKey = visibleNodeSemanticById.get(edge.target);
      const onPinnedPath =
        Boolean(sourcePathId)
        && sourcePathId === targetPathId
        && pinnedPathSet.has(sourcePathId as string);
      const touchesPinnedPath =
        (Boolean(sourcePathId) && pinnedPathSet.has(sourcePathId as string))
        || (Boolean(targetPathId) && pinnedPathSet.has(targetPathId as string));
      const onSemanticFocus =
        activeSemanticKey !== null
        && (sourceSemanticKey === activeSemanticKey || targetSemanticKey === activeSemanticKey);
      const strokeWidth = typeof baseStyle.strokeWidth === 'number' ? baseStyle.strokeWidth : 2;
      return {
        ...baseStyle,
        opacity:
          pinnedPathIds.length > 0
            ? onPinnedPath
              ? 1
              : touchesPinnedPath
                ? 0.56
                : 0.12
            : activeSemanticKey
              ? onSemanticFocus
                ? 1
                : 0.14
              : 1,
        strokeWidth: onPinnedPath || onSemanticFocus ? strokeWidth + 0.8 : strokeWidth,
        filter:
          onPinnedPath
            ? 'drop-shadow(0 0 0.28rem rgba(245,158,11,0.42))'
            : onSemanticFocus && activeSemanticKey
              ? 'drop-shadow(0 0 0.26rem rgba(37,99,235,0.28))'
              : 'none',
        transition: 'opacity 120ms ease, filter 120ms ease',
      };
    })(),
    data: {
      ...(edge.data as Record<string, unknown>),
      appearance,
    },
  }));

  const selectedNode = useMemo(
    () => enrichedNodes.find((node) => node.id === selectedNodeId) ?? null,
    [enrichedNodes, selectedNodeId],
  );
  const selectedEdge = useMemo(
    () => enrichedEdges.find((edge) => edge.id === selectedEdgeId) ?? null,
    [enrichedEdges, selectedEdgeId],
  );
  const selectedEdgeData = useMemo(
    () => (selectedEdge?.data as InvestigationEdge | undefined) ?? null,
    [selectedEdge],
  );
  const selectedNodeData = useMemo(
    () => (selectedNode?.data as InvestigationNode | undefined) ?? null,
    [selectedNode],
  );
  const bridgeStatusRefresh = useBridgeHopPoller({
    sessionId,
    node: selectedNodeData,
    onStatus: updateBridgeHopStatus,
    onNotice: showNotice,
  });
  const visibleInvestigationNodeById = useMemo(
    () =>
      new Map(
        enrichedNodes.map((node) => [node.id, (node.data as unknown as InvestigationNode)]),
      ),
    [enrichedNodes],
  );
  const selectedEdgeSourceNode = useMemo(
    () => (selectedEdge ? visibleInvestigationNodeById.get(selectedEdge.source) ?? null : null),
    [selectedEdge, visibleInvestigationNodeById],
  );
  const selectedEdgeTargetNode = useMemo(
    () => (selectedEdge ? visibleInvestigationNodeById.get(selectedEdge.target) ?? null : null),
    [selectedEdge, visibleInvestigationNodeById],
  );
  const canTraceSelectedEdgeBackward = Boolean(
    selectedEdgeData?.tx_hash
    && selectedEdgeSourceNode?.node_type === 'address'
    && selectedEdgeSourceNode.expandable_directions.includes('prev'),
  );
  const canTraceSelectedEdgeForward = Boolean(
    selectedEdgeData?.tx_hash
    && selectedEdgeTargetNode?.node_type === 'address'
    && selectedEdgeTargetNode.expandable_directions.includes('next'),
  );

  const handleTraceSelectedEdge = useCallback(
    (direction: 'forward' | 'backward') => {
      if (!selectedEdgeData?.tx_hash) {
        showNotice('The selected edge has no transaction hash to focus on.', 'error');
        return;
      }

      const endpoint =
        direction === 'forward' ? selectedEdgeTargetNode : selectedEdgeSourceNode;
      if (!endpoint || endpoint.node_type !== 'address') {
        showNotice(
          'This transaction endpoint is not an address node that can be traced further.',
          'error',
        );
        return;
      }

      const operation = direction === 'forward' ? 'expand_next' : 'expand_prev';
      void handleExpand(endpoint, operation, [selectedEdgeData.tx_hash]);
    },
    [handleExpand, selectedEdgeData, selectedEdgeSourceNode, selectedEdgeTargetNode, showNotice],
  );

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (
        event.defaultPrevented
        || event.metaKey
        || event.ctrlKey
        || event.altKey
      ) {
        return;
      }

      const target = event.target as HTMLElement | null;
      if (
        target
        && (
          target.tagName === 'INPUT'
          || target.tagName === 'TEXTAREA'
          || target.tagName === 'SELECT'
          || target.isContentEditable
        )
      ) {
        return;
      }

      if ((event.key === 'Delete' || event.key === 'Backspace') && selectedNodeId) {
        event.preventDefault();
        handleHideNode(selectedNodeId);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [handleHideNode, selectedNodeId]);

  const selectedPathStory = useMemo(() => {
    if (!selectedNode) return null;
    const data = selectedNode.data as unknown as InvestigationNode;
    return pathStoryById.get(data.path_id) ?? null;
  }, [selectedNode, pathStoryById]);

  const selectedSemanticMeta = useMemo(() => {
    if (!selectedNode) return null;
    return semanticMetaForNode((selectedNode.data as unknown) as InvestigationNode);
  }, [selectedNode]);

  const selectedSemanticCount = useMemo(() => {
    if (!selectedSemanticMeta) return 0;
    return semanticLegend.entries.find((entry) => entry.key === selectedSemanticMeta.key)?.count ?? 0;
  }, [selectedSemanticMeta, semanticLegend.entries]);

  const pinnedPathStories = useMemo(
    () =>
      pinnedPathIds
        .map((pathId) => pathStoryById.get(pathId) ?? null)
        .filter((story): story is PathStory => story !== null),
    [pinnedPathIds, pathStoryById],
  );

  useEffect(() => {
    if (selectedNodeId || selectedEdgeId) {
      setInspectorCollapsed(false);
    }
  }, [selectedNodeId, selectedEdgeId]);

  useEffect(() => {
    if (!filters.bridgeRoute) return;
    setBridgeRouteHistory((current) => [
      filters.bridgeRoute as string,
      ...current.filter((route) => route !== filters.bridgeRoute),
    ].slice(0, 6));
  }, [filters.bridgeRoute]);

  useEffect(() => {
    if (activeBranchIds.length === 0) return;
    setBranchHistory((current) => [
      ...activeBranchIds,
      ...current.filter((branchId) => !activeBranchIds.includes(branchId)),
    ].slice(0, 8));
  }, [activeBranchIds]);

  useEffect(() => {
    const availablePathIds = new Set(pathStories.map((story) => story.pathId));
    setPinnedPathIds((current) => current.filter((pathId) => availablePathIds.has(pathId)));
  }, [pathStories]);

  useEffect(() => {
    if (!activeSemanticKey) return;
    if (!semanticLegend.entries.some((entry) => entry.key === activeSemanticKey)) {
      setActiveSemanticKey(null);
    }
  }, [activeSemanticKey, semanticLegend.entries]);

  useEffect(() => {
    if (!notice?.autoDismiss) return;
    const timeoutId = window.setTimeout(() => {
      setNotice((current) => (current?.message === notice.message ? null : current));
    }, 4200);
    return () => window.clearTimeout(timeoutId);
  }, [notice]);

  const focusBridgeRoute = useCallback((route: string) => {
    setFilters((current) => ({
      ...current,
      bridgeRoute: current.bridgeRoute === route ? null : route,
    }));
  }, []);

  const focusBridgeProtocol = useCallback((protocolId: string) => {
    setFilters((current) => ({
      ...current,
      bridgeProtocols:
        current.bridgeProtocols.length === 1 && current.bridgeProtocols[0] === protocolId
          ? []
          : [protocolId],
    }));
  }, []);

  const clearBridgeFocus = useCallback(() => {
    setFilters((current) => ({
      ...current,
      bridgeProtocols: [],
      bridgeStatuses: [],
      bridgeRoute: null,
    }));
  }, []);

  const focusBranch = useCallback((branchId: string) => {
    setActiveBranchIds((current) =>
      current.length === 1 && current[0] === branchId ? [] : [branchId],
    );
  }, []);

  const compareBranch = useCallback((branchId: string) => {
    setActiveBranchIds((current) => {
      if (current.includes(branchId)) {
        return current.filter((value) => value !== branchId);
      }
      if (current.length === 0) return [branchId];
      if (current.length === 1) return [current[0], branchId];
      return [current[1], branchId];
    });
  }, []);

  const clearBranchFocus = useCallback(() => {
    setActiveBranchIds([]);
  }, []);

  const togglePinnedPath = useCallback((pathId: string) => {
    setPinnedPathIds((current) => {
      if (current.includes(pathId)) {
        return current.filter((value) => value !== pathId);
      }
      return [pathId, ...current.filter((value) => value !== pathId)].slice(0, 4);
    });
  }, []);

  const clearPinnedPaths = useCallback(() => {
    setPinnedPathIds([]);
  }, []);

  const focusSemanticKey = useCallback((key: string) => {
    setActiveSemanticKey((current) => (current === key ? null : key));
  }, []);

  const clearSemanticFocus = useCallback(() => {
    setActiveSemanticKey(null);
  }, []);

  const bridgeSummary = useMemo(() => {
    const bridgeNodes = nodes
      .map((node) => node.data as unknown as InvestigationNode)
      .filter((node) => node.node_type === 'bridge_hop');

    if (bridgeNodes.length === 0) return null;

    const protocols = new Map<string, { protocolId: string; label: string; count: number; color: string }>();
    const routes = new Map<string, number>();
    const statuses = { pending: 0, completed: 0, failed: 0 } as Record<string, number>;

    for (const node of bridgeNodes) {
      const hop = (node.bridge_hop_data ?? node.node_data) as InvestigationNode['bridge_hop_data'];
      if (!hop) continue;

      const protocolId = hop.protocol_id ?? 'unknown';
      const currentProtocol = protocols.get(protocolId) ?? {
        protocolId,
        label: bridgeProtocolLabel(protocolId),
        count: 0,
        color: getBridgeProtocolColor(protocolId),
      };
      currentProtocol.count += 1;
      protocols.set(protocolId, currentProtocol);

      const route = bridgeRouteLabel({
        source_chain: hop.source_chain,
        destination_chain: hop.destination_chain,
      });
      routes.set(route, (routes.get(route) ?? 0) + 1);

      statuses[hop.status] = (statuses[hop.status] ?? 0) + 1;
    }

    return {
      total: bridgeNodes.length,
      protocols: [...protocols.values()].sort((a, b) => b.count - a.count),
      routes: [...routes.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 4),
      statuses,
    };
  }, [nodes]);

  const branchCompareSummaries = useMemo(() => {
    if (activeBranchIds.length === 0) return [];

    return activeBranchIds
      .map((branchId) => {
        const branch = branchMetaById.get(branchId);
        if (!branch) return null;

        const branchNodes = nodes
          .map((node) => node.data as unknown as InvestigationNode)
          .filter((node) => node.branch_id === branchId);
        const branchEdges = edges.filter((edge) => {
          const data = edge.data as Record<string, unknown> | undefined;
          return data?.branch_id === branchId;
        });
        const bridgeHopCount = branchNodes.filter((node) => node.node_type === 'bridge_hop').length;
        const pathCount = new Set(branchNodes.map((node) => node.path_id)).size;
        const pinnedPathCount = new Set(
          branchNodes
            .map((node) => node.path_id)
            .filter((pathId) => pinnedPathIds.includes(pathId)),
        ).size;
        const chains = Array.from(
          new Set(
            branchNodes
              .map((node) => node.chain ?? node.address_data?.chain)
              .filter((value): value is string => Boolean(value)),
          ),
        );
        const semanticCounts = new Map<string, { label: string; color: string; count: number }>();
        for (const node of branchNodes) {
          const meta = semanticMetaForNode(node);
          if (!meta) continue;
          const existing = semanticCounts.get(meta.key) ?? {
            label: meta.label,
            color: meta.color,
            count: 0,
          };
          existing.count += 1;
          semanticCounts.set(meta.key, existing);
        }

        return {
          branch,
          visibleNodes: branchNodes.length,
          visibleEdges: branchEdges.length,
          bridgeHopCount,
          pathCount,
          pinnedPathCount,
          chains,
          topSemantics: [...semanticCounts.values()]
            .sort((a, b) => b.count - a.count)
            .slice(0, 3),
        };
      })
      .filter((summary): summary is NonNullable<typeof summary> => summary !== null);
  }, [activeBranchIds, branchMetaById, edges, nodes, pinnedPathIds]);

  const branchCompareHeadline = useMemo(() => {
    if (branchCompareSummaries.length === 0) return null;
    if (branchCompareSummaries.length === 1) {
      const summary = branchCompareSummaries[0];
      return `${branchLabel(summary.branch)} holds ${summary.visibleNodes} visible nodes across ${summary.pathCount} paths.`;
    }

    const [left, right] = branchCompareSummaries;
    const nodeLeader = left.visibleNodes === right.visibleNodes
      ? null
      : left.visibleNodes > right.visibleNodes
        ? left
        : right;
    const bridgeLeader = left.bridgeHopCount === right.bridgeHopCount
      ? null
      : left.bridgeHopCount > right.bridgeHopCount
        ? left
        : right;

    if (bridgeLeader) {
      const diff = Math.abs(left.bridgeHopCount - right.bridgeHopCount);
      return `${branchLabel(bridgeLeader.branch)} carries ${diff} more bridge hop${diff === 1 ? '' : 's'} in the current lens.`;
    }
    if (nodeLeader) {
      const diff = Math.abs(left.visibleNodes - right.visibleNodes);
      return `${branchLabel(nodeLeader.branch)} carries ${diff} more visible node${diff === 1 ? '' : 's'} in the current lens.`;
    }
    return 'The active branches are balanced on visible node and bridge-hop counts.';
  }, [branchCompareSummaries]);

  const activeSemanticEntry = useMemo(
    () => semanticLegend.entries.find((entry) => entry.key === activeSemanticKey) ?? null,
    [activeSemanticKey, semanticLegend.entries],
  );

  const sessionBriefing = useMemo<SessionBriefing>(() => {
    const currentTimestamp = new Date().toISOString();
    const title = `Session briefing · ${sessionId.slice(0, 8)}`;
    const headlineParts = [
      `${nodes.length} visible nodes`,
      `${edges.length} visible edges`,
      bridgeSummary ? `${bridgeSummary.total} bridge hops in view` : null,
      activeBranchIds.length > 0
        ? `${activeBranchIds.length} active branch${activeBranchIds.length === 1 ? '' : 'es'}`
        : null,
    ].filter((value): value is string => Boolean(value));

    const overviewBullets = [
      `Session ${sessionId}`,
      `Canvas view: ${appearance.viewMode} / ${appearance.interactionMode}`,
      `Visible graph: ${nodes.length} nodes and ${edges.length} edges`,
      `Asset picker scope: ${assetCatalogScope === 'visible' ? 'visible lens' : 'full session'}`,
      pinnedAssetKeys.length > 0 ? `Pinned assets: ${pinnedAssetKeys.length}` : null,
      filters.selectedAssets.length > 0 ? `Selected assets: ${filters.selectedAssets.length}` : null,
      filters.bridgeRoute ? `Route focus: ${filters.bridgeRoute}` : null,
      filters.bridgeProtocols.length > 0
        ? `Bridge protocol focus: ${filters.bridgeProtocols.map((protocolId) => bridgeProtocolLabel(protocolId)).join(', ')}`
        : null,
      activeSemanticEntry ? `Semantic focus: ${activeSemanticEntry.label}` : null,
      activeBranchIds.length > 0
        ? `Branch focus: ${activeBranchIds.map((branchId) => branchLabel(branchMetaById.get(branchId))).join(', ')}`
        : null,
      pinnedPathStories.length > 0
        ? `Pinned paths: ${pinnedPathStories.map((story) => story.summary).join(' | ')}`
        : null,
    ].filter((value): value is string => Boolean(value));

    const bridgeLines = bridgeSummary
      ? [
          `Visible bridge hops: ${bridgeSummary.total}`,
          `Bridge statuses: pending ${bridgeSummary.statuses.pending ?? 0}, completed ${bridgeSummary.statuses.completed ?? 0}${bridgeSummary.statuses.failed ? `, failed ${bridgeSummary.statuses.failed}` : ''}`,
          bridgeSummary.protocols.length > 0
            ? `Top bridge protocols: ${bridgeSummary.protocols.slice(0, 4).map((protocol) => `${protocol.label} (${protocol.count})`).join(', ')}`
            : null,
          bridgeSummary.routes.length > 0
            ? `Dominant routes: ${bridgeSummary.routes.slice(0, 3).map(([route, count]) => `${route} (${count})`).join(', ')}`
            : null,
        ].filter((value): value is string => Boolean(value))
      : [];

    const semanticLines = semanticLegend.entries.length > 0
      ? semanticLegend.entries.slice(0, 6).map((entry) => `${entry.label} [${entry.family}] (${entry.count})`)
      : [];

    const compareLines = branchCompareSummaries.length > 0
      ? branchCompareSummaries.map((summary) => {
          const semanticSummary = summary.topSemantics.length > 0
            ? `; top rails ${summary.topSemantics.map((semantic) => `${semantic.label} (${semantic.count})`).join(', ')}`
            : '';
          return `${branchLabel(summary.branch)}: ${summary.visibleNodes} nodes, ${summary.visibleEdges} edges, ${summary.bridgeHopCount} bridge hops, ${summary.pathCount} paths${summary.pinnedPathCount > 0 ? `, ${summary.pinnedPathCount} pinned` : ''}${semanticSummary}`;
        })
      : [];

    const pinnedPathLines = pinnedPathStories.map((story) => (
      `${story.summary}: ${story.nodeCount} nodes, depth ${story.minDepth}-${story.maxDepth}, ${story.branchCount} branches`
    ));

    const markdown = [
      `# ${title}`,
      '',
      `Generated: ${currentTimestamp}`,
      '',
      '## Headline',
      '',
      headlineParts.join(' · '),
      '',
      '## Current lens',
      '',
      ...overviewBullets.map((line) => `- ${line}`),
      '',
      '## Bridge intelligence',
      '',
      ...(bridgeLines.length > 0 ? bridgeLines.map((line) => `- ${line}`) : ['- No bridge hops in the current visible lens.']),
      '',
      '## Semantic rails in view',
      '',
      ...(semanticLines.length > 0 ? semanticLines.map((line) => `- ${line}`) : ['- No protocol or primitive legend entries are active in the current lens.']),
      '',
      '## Branch compare',
      '',
      ...(branchCompareHeadline ? [`- ${branchCompareHeadline}`] : ['- No branch compare is active.']),
      ...compareLines.map((line) => `- ${line}`),
      '',
      '## Pinned path stories',
      '',
      ...(pinnedPathLines.length > 0 ? pinnedPathLines.map((line) => `- ${line}`) : ['- No pinned paths yet.']),
      '',
      '## Analyst note',
      '',
      'Use this briefing as a session handoff artifact and pair it with the JSON snapshot when another investigator needs the exact same graph state.',
    ].join('\n');

    return {
      title,
      headline: headlineParts.join(' · ') || 'No visible graph state',
      markdown,
    };
  }, [
    activeBranchIds,
    activeSemanticEntry,
    appearance.interactionMode,
    appearance.viewMode,
    assetCatalogScope,
    branchCompareHeadline,
    branchCompareSummaries,
    branchMetaById,
    bridgeSummary,
    edges.length,
    filters.bridgeProtocols,
    filters.bridgeRoute,
    filters.selectedAssets.length,
    nodes.length,
    pinnedAssetKeys.length,
    pinnedPathStories,
    semanticLegend.entries,
    sessionId,
  ]);

  const copyBriefing = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(sessionBriefing.markdown);
    } catch (error) {
      console.error('Failed to copy session briefing:', error);
      alert('Unable to copy the session briefing. Please try the markdown export instead.');
    }
  }, [sessionBriefing.markdown]);

  const downloadBriefing = useCallback(() => {
    const blobUrl = URL.createObjectURL(new Blob([sessionBriefing.markdown], { type: 'text/markdown;charset=utf-8' }));
    const anchor = document.createElement('a');
    anchor.href = blobUrl;
    anchor.download = `session-briefing-${sessionId.slice(0, 8)}.md`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(blobUrl), 100);
  }, [sessionBriefing.markdown, sessionId]);

  return (
    <IngestPendingContext.Provider value={{ pendingNodeIds: ingestPendingNodeIds }}>
      {/* Render-null pollers — one per pending node, respecting rules-of-hooks */}
      {Array.from(ingestPendingMap.entries()).map(([nodeId, { address, chain }]) => (
        <IngestPoller
          key={nodeId}
          sessionId={sessionId}
          nodeId={nodeId}
          address={address}
          chain={chain}
          onComplete={handleIngestComplete}
          onUnavailable={handleIngestUnavailable}
          onTimeout={handleIngestTimeout}
        />
      ))}
    <div
      style={{
        width: '100vw',
        height: '100vh',
        background:
          'radial-gradient(circle at top left, rgba(191,219,254,0.28), transparent 30%), linear-gradient(180deg, #f8fafc 0%, #eef2ff 100%)',
        position: 'relative',
      }}
    >

      {/* Toolbar */}
      <div
        style={{
          position: 'absolute',
          top: 16,
          left: 16,
          zIndex: 100,
          display: 'flex',
          gap: 10,
          alignItems: 'center',
          flexWrap: 'wrap',
        }}
      >
        <button
          onClick={() => {
            setAppearanceVisible(false);
            setFilterVisible((v) => !v);
          }}
          style={toolbarBtnStyle}
        >
          Filters
          {[
            filters.chainFilter.length > 0,
            filters.minFiatValue !== null && filters.minFiatValue > 0,
            filters.maxDepth !== undefined && filters.maxDepth < 20,
            filters.assetFilter !== undefined && filters.assetFilter.length > 0,
            filters.selectedAssets.length > 0,
            filters.bridgeProtocols.length > 0,
            filters.bridgeStatuses.length > 0,
            Boolean(filters.bridgeRoute),
          ].filter(Boolean).length > 0 ? ' •' : ''}
        </button>
        <button
          onClick={() => {
            setFilterVisible(false);
            setAppearanceVisible((v) => !v);
          }}
          style={toolbarBtnStyle}
        >
          Appearance
        </button>
        <button
          onClick={() => {
            const json = exportSnapshot({
              workspacePreferences: currentSnapshotWorkspacePreferences,
            });
            const a = document.createElement('a');
            const blobUrl = URL.createObjectURL(new Blob([json], { type: 'application/json' }));
            a.href = blobUrl;
            a.download = `session-${sessionId.slice(0, 8)}.json`;
            a.click();
            // Clean up blob URL to prevent memory leaks
            setTimeout(() => URL.revokeObjectURL(blobUrl), 100);
          }}
          style={toolbarBtnStyle}
          title="Save session snapshot"
        >
          Export
        </button>
        <label style={{ ...toolbarBtnStyle, cursor: 'pointer' }} title="Restore session snapshot">
          Import
          <input
            type="file"
            accept=".json"
            style={{ display: 'none' }}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              file.text()
                .then((text) => {
                  layoutedNodeIds.current.clear();
                  didHydrateManualLayoutRef.current = false;
                  const restored = importSnapshot(text);
                  if (!restored) {
                    throw new Error('Snapshot import failed.');
                  }
                  const snapshotPreferences = extractSnapshotWorkspacePreferences(text);
                  if (snapshotPreferences) {
                    setFilters((current) => ({
                      ...current,
                      selectedAssets: snapshotPreferences.selectedAssets,
                    }));
                    setPinnedAssetKeys(snapshotPreferences.pinnedAssetKeys);
                    setAssetCatalogScope(snapshotPreferences.assetCatalogScope);
                    saveSessionWorkspacePreferences(sessionId, snapshotPreferences);
                  } else {
                    setFilters((current) => ({ ...current, selectedAssets: [] }));
                    setPinnedAssetKeys([]);
                    setAssetCatalogScope(DEFAULT_ASSET_CATALOG_SCOPE);
                  }
                  showNotice('Snapshot restored. Manual positions, removed nodes, and asset workspace state are back on canvas.');
                  e.target.value = '';
                })
                .catch((error) => {
                  console.error('Failed to import session snapshot:', error);
                  alert('Failed to import session snapshot. Please check the file format.');
                  e.target.value = '';
                });
            }}
          />
        </label>
        <button
          onClick={() => setBriefingVisible((value) => !value)}
          style={toolbarBtnStyle}
          title="Open session briefing"
        >
          Briefing
          {[
            activeBranchIds.length > 0,
            pinnedPathIds.length > 0,
            Boolean(filters.bridgeRoute),
            filters.bridgeProtocols.length > 0,
            Boolean(activeSemanticKey),
          ].some(Boolean) ? ' •' : ''}
        </button>
        <button
          type="button"
          onClick={handleReframeGraph}
          style={toolbarBtnStyle}
          title="Reframe the visible graph without changing node positions"
        >
          Reframe
        </button>
        {hiddenNodeCount > 0 && (
          <button
            type="button"
            onClick={() => {
              restoreAllHiddenNodes();
              showNotice('Removed nodes restored to the canvas.');
            }}
            style={toolbarBtnStyle}
            title="Restore nodes that were manually removed from the canvas"
          >
            Restore Removed · {hiddenNodeCount}
          </button>
        )}
        <button
          type="button"
          onClick={() => {
            const confirmed = window.confirm(
              'Start a new investigation? This clears the current graph from the canvas but keeps you signed in.',
            );
            if (!confirmed) return;
            onStartNewInvestigation();
          }}
          style={{
            ...toolbarBtnStyle,
            borderColor: 'rgba(37,99,235,0.28)',
            color: '#1d4ed8',
          }}
          title="Clear the current graph and return to a fresh search"
        >
          New Investigation
        </button>
        <span style={toolbarPillStyle}>
          {appearance.viewMode} view
        </span>
        <span style={toolbarPillStyle}>
          {appearance.interactionMode} mode
        </span>
        <span
          style={{
            ...toolbarPillStyle,
            color:
              sessionSaveStatus === 'save_failed'
                ? '#b91c1c'
                : sessionSaveStatus === 'dirty'
                  ? '#92400e'
                : sessionSaveStatus === 'saving'
                  ? '#1d4ed8'
                  : '#334155',
            borderColor:
              sessionSaveStatus === 'save_failed'
                ? 'rgba(185,28,28,0.18)'
                : sessionSaveStatus === 'dirty'
                  ? 'rgba(146,64,14,0.2)'
                : sessionSaveStatus === 'saving'
                  ? 'rgba(37,99,235,0.18)'
                  : toolbarPillStyle.borderColor,
            background:
              sessionSaveStatus === 'save_failed'
                ? 'rgba(254,242,242,0.96)'
                : sessionSaveStatus === 'dirty'
                  ? 'rgba(255,247,237,0.96)'
                : sessionSaveStatus === 'saving'
                  ? 'rgba(239,246,255,0.96)'
                  : toolbarPillStyle.background,
          }}
          title="Server-backed session save status"
        >
          {sessionSaveLabel}
        </span>
        <span style={{ color: '#475569', fontSize: 12, alignSelf: 'center', fontWeight: 600 }}>
          {rfNodes.length} nodes · {rfEdges.length} edges
        </span>
      </div>

      {notice && (
        <div
          style={{
            position: 'absolute',
            top: 72,
            left: '50%',
            transform: 'translateX(-50%)',
            zIndex: 108,
            width: 'min(92vw, 620px)',
            padding: '12px 16px',
            borderRadius: 18,
            background:
              notice.tone === 'error'
                ? 'rgba(127,29,29,0.94)'
                : 'rgba(15,23,42,0.92)',
            border:
              notice.tone === 'error'
                ? '1px solid rgba(252,165,165,0.34)'
                : '1px solid rgba(148,163,184,0.24)',
            boxShadow: '0 16px 36px rgba(15,23,42,0.16)',
            color: '#f8fafc',
            display: 'flex',
            gap: 12,
            alignItems: 'flex-start',
          }}
        >
          <div
            style={{
              color: notice.tone === 'error' ? '#fecaca' : '#93c5fd',
              fontSize: 10,
              fontWeight: 800,
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
              paddingTop: 3,
              whiteSpace: 'nowrap',
            }}
          >
            {notice.tone === 'error' ? 'Expand error' : 'Investigation note'}
          </div>
          <div style={{ flex: 1, fontSize: 12, lineHeight: 1.55 }}>
            {notice.message}
          </div>
          <button
            type="button"
            onClick={() => setNotice(null)}
            style={{
              border: 'none',
              background: 'transparent',
              color: '#e2e8f0',
              fontSize: 16,
              cursor: 'pointer',
              lineHeight: 1,
            }}
            aria-label="Dismiss investigation notice"
          >
            x
          </button>
        </div>
      )}

      {(filters.bridgeRoute || bridgeRouteHistory.length > 0 || filters.bridgeProtocols.length > 0) && (
        <div
          style={{
            position: 'absolute',
            top: 72,
            left: bridgeSummary ? 332 : 16,
            zIndex: 105,
            maxWidth: 620,
            display: 'flex',
            flexWrap: 'wrap',
            gap: 8,
            alignItems: 'center',
            padding: '10px 12px',
            borderRadius: 18,
            background: 'rgba(255,255,255,0.92)',
            border: '1px solid rgba(148,163,184,0.26)',
            boxShadow: '0 14px 36px rgba(15,23,42,0.10)',
            backdropFilter: 'blur(12px)',
          }}
        >
          <span style={routeFocusEyebrowStyle}>Route focus</span>
          {filters.bridgeRoute && (
            <button
              type="button"
              onClick={() => focusBridgeRoute(filters.bridgeRoute as string)}
              style={{
                ...routeChipStyle('#7c3aed'),
                background: 'rgba(124,58,237,0.14)',
              }}
            >
              {filters.bridgeRoute} · active
            </button>
          )}
          {filters.bridgeProtocols.map((protocolId) => (
            <button
              key={protocolId}
              type="button"
              onClick={() => focusBridgeProtocol(protocolId)}
              style={{
                ...routeChipStyle('#1d4ed8'),
                background: 'rgba(37,99,235,0.14)',
              }}
            >
              {bridgeProtocolLabel(protocolId)} · protocol
            </button>
          ))}
          {bridgeRouteHistory
            .filter((route) => route !== filters.bridgeRoute)
            .slice(0, 4)
            .map((route) => (
              <button
                key={route}
                type="button"
                onClick={() => focusBridgeRoute(route)}
                style={routeChipStyle('#475569')}
              >
                {route}
              </button>
            ))}
          {(filters.bridgeRoute || filters.bridgeProtocols.length > 0 || filters.bridgeStatuses.length > 0) && (
            <button
              type="button"
              onClick={clearBridgeFocus}
              style={clearRouteButtonStyle}
            >
              Clear
            </button>
          )}
        </div>
      )}

      {(activeBranchIds.length > 0 || branchHistory.length > 0 || branchEntries.length > 1) && (
        <aside
          style={{
            position: 'absolute',
            top: bridgeSummary ? 304 : 72,
            left: 16,
            zIndex: 104,
            width: 320,
            padding: '14px 16px',
            borderRadius: 20,
            background: 'rgba(255,255,255,0.94)',
            border: '1px solid rgba(59,130,246,0.16)',
            boxShadow: '0 18px 40px rgba(15,23,42,0.10)',
            backdropFilter: 'blur(14px)',
            color: '#0f172a',
          }}
        >
          <div style={{ color: '#2563eb', fontSize: 10, fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
            Branch workspace
          </div>
          <div style={{ marginTop: 6, fontSize: 21, fontWeight: 800 }}>
            {activeBranchIds.length === 0
              ? `${branchEntries.length} active branches`
              : activeBranchIds.length === 1
                ? 'Focused branch'
                : 'Compare branches'}
          </div>
          {activeBranchIds.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
              {activeBranchIds.map((branchId) => {
                const branch = branchMetaById.get(branchId);
                return (
                  <button
                    key={branchId}
                    type="button"
                    onClick={() => focusBranch(branchId)}
                    style={{
                      ...branchChipStyle(branch?.color ?? '#2563eb'),
                      background: `${branch?.color ?? '#2563eb'}18`,
                    }}
                  >
                    {branchLabel(branch)}
                  </button>
                );
              })}
              <button type="button" onClick={clearBranchFocus} style={clearRouteButtonStyle}>
                Clear
              </button>
            </div>
          )}
          <div style={{ marginTop: 12, display: 'grid', gap: 8 }}>
            {branchEntries.slice(0, 6).map((branch) => {
              const active = activeBranchIds.includes(branch.branchId);
              return (
                <div
                  key={branch.branchId}
                  style={{
                    border: `1px solid ${branch.color}24`,
                    borderRadius: 14,
                    padding: '10px 12px',
                    background: active ? `${branch.color}12` : 'rgba(255,255,255,0.88)',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
                    <div>
                      <div style={{ color: branch.color, fontWeight: 800, fontSize: 12 }}>
                        {branchLabel(branch)}
                      </div>
                      <div style={{ color: '#64748b', fontSize: 11, marginTop: 4 }}>
                        {branch.nodeCount} nodes · depth {branch.minDepth}-{branch.maxDepth}
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button
                        type="button"
                        onClick={() => focusBranch(branch.branchId)}
                        style={{
                          ...miniActionStyle,
                          color: active && activeBranchIds.length === 1 ? branch.color : '#334155',
                          borderColor: `${branch.color}24`,
                        }}
                      >
                        {active && activeBranchIds.length === 1 ? 'Focused' : 'Focus'}
                      </button>
                      <button
                        type="button"
                        onClick={() => compareBranch(branch.branchId)}
                        style={{
                          ...miniActionStyle,
                          color: active && activeBranchIds.length > 1 ? branch.color : '#334155',
                          borderColor: `${branch.color}24`,
                        }}
                      >
                        {active && activeBranchIds.length > 1 ? 'Compared' : 'Compare'}
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
          {branchHistory.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <div style={summaryHeadingStyle}>Recent branches</div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 6 }}>
                {branchHistory
                  .filter((branchId) => !activeBranchIds.includes(branchId))
                  .slice(0, 4)
                  .map((branchId) => {
                    const branch = branchMetaById.get(branchId);
                    if (!branch) return null;
                    return (
                      <button
                        key={branchId}
                        type="button"
                        onClick={() => focusBranch(branchId)}
                        style={branchChipStyle(branch.color)}
                      >
                        {branchLabel(branch)}
                      </button>
                    );
                  })}
              </div>
            </div>
          )}
        </aside>
      )}

      {bridgeSummary && (
        <aside
          style={{
            position: 'absolute',
            top: 72,
            left: 16,
            zIndex: 100,
            width: 300,
            padding: '14px 16px',
            borderRadius: 20,
            background: 'rgba(255,255,255,0.94)',
            border: '1px solid rgba(124, 58, 237, 0.16)',
            boxShadow: '0 18px 40px rgba(15, 23, 42, 0.12)',
            backdropFilter: 'blur(14px)',
            color: '#0f172a',
          }}
        >
          <div style={{ color: '#7c3aed', fontSize: 10, fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
            Bridge intelligence
          </div>
          <div style={{ marginTop: 6, fontSize: 22, fontWeight: 800 }}>
            {bridgeSummary.total} visible hops
          </div>
          <div style={{ marginTop: 6, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <span style={summaryChipStyle('#f59e0b')}>
              {bridgeSummary.statuses.pending ?? 0} pending
            </span>
            <span style={summaryChipStyle('#10b981')}>
              {bridgeSummary.statuses.completed ?? 0} completed
            </span>
            {!!bridgeSummary.statuses.failed && (
              <span style={summaryChipStyle('#ef4444')}>
                {bridgeSummary.statuses.failed} failed
              </span>
            )}
          </div>
          <div style={{ marginTop: 12, display: 'grid', gap: 10 }}>
            <div>
              <div style={summaryHeadingStyle}>Protocols in view</div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 6 }}>
                {bridgeSummary.protocols.slice(0, 6).map((protocol) => (
                  <button
                    key={protocol.label}
                    type="button"
                    onClick={() => focusBridgeProtocol(protocol.protocolId)}
                    style={{
                      ...summaryChipStyle(protocol.color),
                      fontWeight: 700,
                      cursor: 'pointer',
                      background: filters.bridgeProtocols.includes(protocol.protocolId)
                        ? `${protocol.color}24`
                        : `${protocol.color}14`,
                    }}
                  >
                    {protocol.label} · {protocol.count}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <div style={summaryHeadingStyle}>Dominant routes</div>
              <div style={{ display: 'grid', gap: 6, marginTop: 6 }}>
                {bridgeSummary.routes.map(([route, count]) => (
                  <button
                    key={route}
                    type="button"
                    onClick={() => focusBridgeRoute(route)}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      gap: 12,
                      fontSize: 12,
                      color: '#334155',
                      border: 'none',
                      background:
                        filters.bridgeRoute === route
                          ? 'rgba(124, 58, 237, 0.12)'
                          : 'transparent',
                      borderRadius: 10,
                      padding: '6px 8px',
                      cursor: 'pointer',
                      textAlign: 'left',
                    }}
                  >
                    <span>{route}</span>
                    <span style={{ color: '#7c3aed', fontWeight: 700 }}>{count}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </aside>
      )}

      {semanticLegend.entries.length > 0 && (
        <aside
          style={{
            position: 'absolute',
            top: bridgeSummary ? 72 : 72,
            right: inspectorCollapsed ? 92 : 376,
            zIndex: 101,
            width: 320,
            padding: '14px 16px',
            borderRadius: 20,
            background: 'rgba(255,255,255,0.94)',
            border: '1px solid rgba(15, 118, 110, 0.16)',
            boxShadow: '0 18px 40px rgba(15, 23, 42, 0.10)',
            backdropFilter: 'blur(14px)',
            color: '#0f172a',
          }}
        >
          <div style={{ color: '#0f766e', fontSize: 10, fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
            Protocol legend
          </div>
          <div style={{ marginTop: 6, fontSize: 21, fontWeight: 800 }}>
            Semantic surfaces in view
          </div>
          <div style={{ color: '#475569', fontSize: 12, marginTop: 6, lineHeight: 1.5 }}>
            Focus a protocol or primitive family to quiet the graph and compare the same kind
            of flow across routes and branches.
          </div>

          <div style={{ marginTop: 12 }}>
            <div style={summaryHeadingStyle}>Primitive families</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
              {semanticLegend.families.slice(0, 6).map((family) => (
                <span
                  key={family.family}
                  style={{
                    ...summaryChipStyle(family.color),
                    cursor: 'default',
                  }}
                >
                  {family.family} · {family.count}
                </span>
              ))}
            </div>
          </div>

          <div style={{ marginTop: 14 }}>
            <div style={summaryHeadingStyle}>Protocols and semantic rails</div>
            <div style={{ display: 'grid', gap: 8, marginTop: 8 }}>
              {semanticLegend.entries.slice(0, 8).map((entry) => {
                const active = activeSemanticKey === entry.key;
                return (
                  <button
                    key={entry.key}
                    type="button"
                    onClick={() => focusSemanticKey(entry.key)}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      gap: 12,
                      alignItems: 'center',
                      border: `1px solid ${entry.color}${active ? '44' : '20'}`,
                      background: active ? `${entry.color}16` : 'rgba(255,255,255,0.88)',
                      borderRadius: 14,
                      padding: '8px 10px',
                      cursor: 'pointer',
                      textAlign: 'left',
                    }}
                  >
                    <div>
                      <div style={{ color: entry.color, fontSize: 12, fontWeight: 800 }}>
                        {entry.label}
                      </div>
                      <div style={{ color: '#64748b', fontSize: 11, marginTop: 3 }}>
                        {entry.family}
                      </div>
                    </div>
                    <span style={summaryChipStyle(entry.color)}>
                      {entry.count}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          {activeSemanticKey && (
            <button
              type="button"
              onClick={clearSemanticFocus}
              style={{ ...clearRouteButtonStyle, marginTop: 12 }}
            >
              Clear protocol focus
            </button>
          )}
        </aside>
      )}

      {branchCompareSummaries.length > 0 && (
        <aside
          style={{
            position: 'absolute',
            left: 16,
            bottom: 24,
            zIndex: 103,
            width: 360,
            padding: '16px 18px',
            borderRadius: 22,
            background: 'rgba(255,255,255,0.95)',
            border: '1px solid rgba(37,99,235,0.16)',
            boxShadow: '0 20px 44px rgba(15,23,42,0.12)',
            backdropFilter: 'blur(14px)',
            color: '#0f172a',
          }}
        >
          <div style={{ color: '#2563eb', fontSize: 10, fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
            Compare briefing
          </div>
          <div style={{ marginTop: 6, fontSize: 22, fontWeight: 800 }}>
            {branchCompareSummaries.length === 1 ? 'Branch snapshot' : 'Branch compare'}
          </div>
          {branchCompareHeadline && (
            <div style={{ color: '#475569', fontSize: 12, lineHeight: 1.55, marginTop: 8 }}>
              {branchCompareHeadline}
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
            {filters.bridgeRoute && (
              <span style={summaryChipStyle('#7c3aed')}>{filters.bridgeRoute}</span>
            )}
            {filters.bridgeProtocols.map((protocolId) => (
              <span key={protocolId} style={summaryChipStyle(getBridgeProtocolColor(protocolId))}>
                {bridgeProtocolLabel(protocolId)}
              </span>
            ))}
            {activeSemanticKey && semanticLegend.entries.find((entry) => entry.key === activeSemanticKey) && (
              <span
                style={summaryChipStyle(
                  semanticLegend.entries.find((entry) => entry.key === activeSemanticKey)?.color ?? '#0f766e',
                )}
              >
                {semanticLegend.entries.find((entry) => entry.key === activeSemanticKey)?.label}
              </span>
            )}
            {pinnedPathIds.length > 0 && (
              <span style={summaryChipStyle('#b45309')}>
                {pinnedPathIds.length} pinned path{pinnedPathIds.length === 1 ? '' : 's'}
              </span>
            )}
          </div>

          <div style={{ display: 'grid', gap: 10, marginTop: 14 }}>
            {branchCompareSummaries.map((summary) => (
              <div
                key={summary.branch.branchId}
                style={{
                  borderRadius: 16,
                  padding: '12px 14px',
                  border: `1px solid ${summary.branch.color}28`,
                  background: `${summary.branch.color}10`,
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center' }}>
                  <div>
                    <div style={{ color: summary.branch.color, fontSize: 12, fontWeight: 800 }}>
                      {branchLabel(summary.branch)}
                    </div>
                    <div style={{ color: '#64748b', fontSize: 11, marginTop: 4 }}>
                      depth {summary.branch.minDepth}-{summary.branch.maxDepth}
                      {' · '}
                      {summary.chains.length > 0 ? summary.chains.join(', ') : 'mixed chains'}
                    </div>
                  </div>
                  <span style={summaryChipStyle(summary.branch.color)}>
                    {summary.visibleNodes} nodes
                  </span>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 8, marginTop: 12 }}>
                  <div style={compareMetricCardStyle}>
                    <div style={compareMetricLabelStyle}>Visible edges</div>
                    <div style={compareMetricValueStyle}>{summary.visibleEdges}</div>
                  </div>
                  <div style={compareMetricCardStyle}>
                    <div style={compareMetricLabelStyle}>Bridge hops</div>
                    <div style={compareMetricValueStyle}>{summary.bridgeHopCount}</div>
                  </div>
                  <div style={compareMetricCardStyle}>
                    <div style={compareMetricLabelStyle}>Paths</div>
                    <div style={compareMetricValueStyle}>{summary.pathCount}</div>
                  </div>
                  <div style={compareMetricCardStyle}>
                    <div style={compareMetricLabelStyle}>Pinned paths</div>
                    <div style={compareMetricValueStyle}>{summary.pinnedPathCount}</div>
                  </div>
                </div>

                {summary.topSemantics.length > 0 && (
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
                    {summary.topSemantics.map((semantic) => (
                      <span key={`${summary.branch.branchId}-${semantic.label}`} style={summaryChipStyle(semantic.color)}>
                        {semantic.label} · {semantic.count}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </aside>
      )}

      {briefingVisible && (
        <aside
          style={{
            position: 'absolute',
            right: inspectorCollapsed ? 92 : 376,
            bottom: 24,
            zIndex: 104,
            width: 380,
            maxHeight: '48vh',
            padding: '16px 18px',
            borderRadius: 22,
            background: 'rgba(255,255,255,0.96)',
            border: '1px solid rgba(180,83,9,0.16)',
            boxShadow: '0 20px 44px rgba(15,23,42,0.12)',
            backdropFilter: 'blur(14px)',
            color: '#0f172a',
            display: 'grid',
            gap: 12,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
            <div>
              <div style={{ color: '#b45309', fontSize: 10, fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
                Session briefing
              </div>
              <div style={{ marginTop: 6, fontSize: 22, fontWeight: 800 }}>
                {sessionBriefing.title}
              </div>
              <div style={{ color: '#475569', fontSize: 12, lineHeight: 1.55, marginTop: 8 }}>
                {sessionBriefing.headline}
              </div>
            </div>
            <button
              type="button"
              onClick={() => setBriefingVisible(false)}
              style={briefingCloseButtonStyle}
              aria-label="Close session briefing"
            >
              x
            </button>
          </div>

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button type="button" onClick={copyBriefing} style={briefingActionButtonStyle('#b45309')}>
              Copy markdown
            </button>
            <button type="button" onClick={downloadBriefing} style={briefingActionButtonStyle('#2563eb')}>
              Download .md
            </button>
            <button
              type="button"
              onClick={() => {
                const json = exportSnapshot();
                const a = document.createElement('a');
                const blobUrl = URL.createObjectURL(new Blob([json], { type: 'application/json' }));
                a.href = blobUrl;
                a.download = `session-${sessionId.slice(0, 8)}.json`;
                a.click();
                setTimeout(() => URL.revokeObjectURL(blobUrl), 100);
              }}
              style={briefingActionButtonStyle('#475569')}
            >
              Download snapshot
            </button>
          </div>

          <div
            style={{
              borderRadius: 16,
              border: '1px solid rgba(148,163,184,0.18)',
              background: 'rgba(248,250,252,0.94)',
              padding: '12px 14px',
              overflow: 'auto',
              whiteSpace: 'pre-wrap',
              fontFamily: '"IBM Plex Mono", "SFMono-Regular", monospace',
              fontSize: 11,
              lineHeight: 1.6,
              color: '#1e293b',
            }}
          >
            {sessionBriefing.markdown}
          </div>
        </aside>
      )}

      {/* Filter panel */}
      {filterVisible && (
        <FilterPanel
          filters={filters}
          onChange={setFilters}
          onClose={() => setFilterVisible(false)}
          availableAssets={availableAssets}
          sessionAssetCount={sessionAvailableAssets.length}
          visibleAssetCount={visibleLensAssets.length}
          assetCatalogScope={assetCatalogScope}
          onAssetCatalogScopeChange={setAssetCatalogScope}
          pinnedAssetKeys={pinnedAssetKeys}
          onPinnedAssetKeysChange={setPinnedAssetKeys}
          availableBridgeProtocols={bridgeFilterOptions.protocols}
          availableBridgeRoutes={bridgeFilterOptions.routes}
        />
      )}
      <GraphAppearancePanel
        appearance={appearance}
        visible={appearanceVisible}
        onClose={() => setAppearanceVisible(false)}
        onChange={setAppearance}
      />

      <ReactFlow
        nodes={enrichedNodes}
        edges={enrichedEdges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        onNodesChange={handleNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        onNodeDragStop={handleNodeDragStop}
        onEdgeClick={handleEdgeClick}
        onPaneClick={() => {
          setSelectedNodeId(null);
          setSelectedEdgeId(null);
        }}
        onInit={(instance) => {
          reactFlowRef.current = instance;
          scheduleFitView(0);
        }}
        fitView
        fitViewOptions={{
          padding: 0.2,
          includeHiddenNodes: false,
          maxZoom: 1.15,
        }}
        minZoom={0.1}
        maxZoom={2.2}
        panOnDrag={appearance.interactionMode === 'grab'}
        nodesDraggable={appearance.interactionMode === 'move'}
        selectionOnDrag={appearance.interactionMode === 'move'}
      >
        {appearance.showGrid && (
          <Background color="#cbd5e1" gap={24} size={1.25} variant={BackgroundVariant.Dots} />
        )}
        <Controls />
        {appearance.showMiniMap && (
          <MiniMap
            nodeColor={(n) => (n.data as { branch_color?: string }).branch_color ?? '#3b82f6'}
            style={{
              background: 'rgba(255,255,255,0.94)',
              border: '1px solid rgba(148, 163, 184, 0.4)',
            }}
            maskColor="rgba(226,232,240,0.65)"
          />
        )}
      </ReactFlow>

      <GraphInspectorPanel
        node={selectedNode}
        edge={selectedEdge}
        collapsed={inspectorCollapsed}
        bridgeStatusRefresh={bridgeStatusRefresh}
        activeBridgeRoute={filters.bridgeRoute}
        activeBridgeProtocols={filters.bridgeProtocols}
        activeBranchIds={activeBranchIds}
        branchMeta={selectedNode ? branchMetaById.get((selectedNode.data as unknown as InvestigationNode).branch_id) ?? null : null}
        pinnedPathIds={pinnedPathIds}
        pinnedPaths={pinnedPathStories}
        pathStory={selectedPathStory}
        semanticMeta={selectedSemanticMeta}
        semanticVisibleCount={selectedSemanticCount}
        activeSemanticKey={activeSemanticKey}
        canTraceEdgeBackward={canTraceSelectedEdgeBackward}
        canTraceEdgeForward={canTraceSelectedEdgeForward}
        onTraceEdgeBackward={() => handleTraceSelectedEdge('backward')}
        onTraceEdgeForward={() => handleTraceSelectedEdge('forward')}
        onExpandNode={(operation) => {
          const nodeData = (selectedNode?.data as InvestigationNode | undefined) ?? null;
          if (!nodeData) return;
          void handleExpand(nodeData, operation);
        }}
        onHideNode={handleHideNode}
        onClose={() => {
          setSelectedNodeId(null);
          setSelectedEdgeId(null);
        }}
        onFocusBranch={focusBranch}
        onCompareBranch={compareBranch}
        onClearBranchFocus={clearBranchFocus}
        onTogglePinnedPath={togglePinnedPath}
        onClearPinnedPaths={clearPinnedPaths}
        onFocusSemanticKey={focusSemanticKey}
        onClearSemanticFocus={clearSemanticFocus}
        onFocusBridgeRoute={focusBridgeRoute}
        onFocusBridgeProtocol={focusBridgeProtocol}
        onClearBridgeFocus={clearBridgeFocus}
        onToggleCollapsed={() => setInspectorCollapsed((value) => !value)}
      />
    </div>
    </IngestPendingContext.Provider>
  );
}

const toolbarBtnStyle: React.CSSProperties = {
  padding: '8px 14px',
  background: 'rgba(255,255,255,0.9)',
  border: '1px solid rgba(148, 163, 184, 0.4)',
  borderRadius: 999,
  color: '#0f172a',
  fontSize: 12,
  fontWeight: 700,
  cursor: 'pointer',
  fontFamily: '"IBM Plex Sans", "Segoe UI", sans-serif',
  boxShadow: '0 8px 24px rgba(15, 23, 42, 0.08)',
};

const toolbarPillStyle: React.CSSProperties = {
  padding: '7px 12px',
  borderRadius: 999,
  background: 'rgba(219, 234, 254, 0.9)',
  border: '1px solid rgba(96, 165, 250, 0.36)',
  color: '#1d4ed8',
  fontSize: 12,
  fontWeight: 700,
  textTransform: 'capitalize',
};

const summaryHeadingStyle: React.CSSProperties = {
  color: '#64748b',
  fontSize: 10,
  fontWeight: 800,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
};

function summaryChipStyle(tone: string): React.CSSProperties {
  return {
    padding: '4px 10px',
    borderRadius: 999,
    background: `${tone}14`,
    border: `1px solid ${tone}24`,
    color: tone,
    fontSize: 11,
    fontWeight: 700,
  };
}

const routeFocusEyebrowStyle: React.CSSProperties = {
  color: '#64748b',
  fontSize: 10,
  fontWeight: 800,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  marginRight: 2,
};

function routeChipStyle(tone: string): React.CSSProperties {
  return {
    padding: '6px 10px',
    borderRadius: 999,
    border: `1px solid ${tone}24`,
    background: 'rgba(255,255,255,0.88)',
    color: tone,
    fontSize: 11,
    fontWeight: 700,
    cursor: 'pointer',
  };
}

function branchLabel(branch: BranchMeta | undefined | null): string {
  if (!branch) return 'Unknown branch';
  return branch.minDepth === 0 ? 'Root branch' : `Branch ${branch.branchId.slice(0, 6)}`;
}

function expandOperationLabel(operation: ExpandRequest['operation_type']): string {
  switch (operation) {
    case 'expand_prev':
      return 'previous';
    case 'expand_next':
      return 'next';
    case 'expand_neighbors':
      return 'neighbor';
    default:
      return 'related';
  }
}

function pathStoryNodeLabel(node: InvestigationNode): string {
  if (node.display_label) return node.display_label;
  if (node.entity_name) return node.entity_name;

  switch (node.node_type) {
    case 'address':
      return node.address_data?.entity_name ?? node.address_data?.address ?? `Address ${node.node_id.slice(0, 6)}`;
    case 'bridge_hop':
      return bridgeProtocolLabel(node.bridge_hop_data?.protocol_id);
    case 'service':
    case 'entity':
      return node.display_sublabel ?? node.node_type.replace(/_/g, ' ');
    default:
      return node.node_type.replace(/_/g, ' ');
  }
}

function branchChipStyle(tone: string): React.CSSProperties {
  return {
    padding: '6px 10px',
    borderRadius: 999,
    border: `1px solid ${tone}24`,
    background: 'rgba(255,255,255,0.88)',
    color: tone,
    fontSize: 11,
    fontWeight: 700,
    cursor: 'pointer',
  };
}

const miniActionStyle: React.CSSProperties = {
  padding: '6px 9px',
  borderRadius: 999,
  border: '1px solid rgba(148,163,184,0.24)',
  background: 'rgba(255,255,255,0.9)',
  fontSize: 11,
  fontWeight: 700,
  cursor: 'pointer',
};

const clearRouteButtonStyle: React.CSSProperties = {
  padding: '6px 10px',
  borderRadius: 999,
  border: '1px solid rgba(148,163,184,0.26)',
  background: 'rgba(255,255,255,0.88)',
  color: '#475569',
  fontSize: 11,
  fontWeight: 700,
  cursor: 'pointer',
};

const compareMetricCardStyle: React.CSSProperties = {
  borderRadius: 12,
  border: '1px solid rgba(148,163,184,0.18)',
  background: 'rgba(255,255,255,0.82)',
  padding: '10px 12px',
};

const compareMetricLabelStyle: React.CSSProperties = {
  color: '#64748b',
  fontSize: 10,
  fontWeight: 800,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
};

const compareMetricValueStyle: React.CSSProperties = {
  color: '#0f172a',
  fontSize: 18,
  fontWeight: 800,
  marginTop: 4,
};

function briefingActionButtonStyle(tone: string): React.CSSProperties {
  return {
    padding: '7px 12px',
    borderRadius: 999,
    border: `1px solid ${tone}2a`,
    background: `${tone}12`,
    color: tone,
    fontSize: 11,
    fontWeight: 800,
    cursor: 'pointer',
  };
}

const briefingCloseButtonStyle: React.CSSProperties = {
  width: 30,
  height: 30,
  borderRadius: 10,
  border: '1px solid rgba(148,163,184,0.26)',
  background: 'rgba(255,255,255,0.88)',
  color: '#475569',
  fontSize: 15,
  lineHeight: 1,
  cursor: 'pointer',
  fontWeight: 700,
};
