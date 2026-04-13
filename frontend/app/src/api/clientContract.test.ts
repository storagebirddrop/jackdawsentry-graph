// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, expandNode, saveSessionSnapshot } from './client';

describe('expandNode contract normalization', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    sessionStorage.clear();
  });

  it('normalizes canonical backend delta fields into the frontend alias shape', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          session_id: 'sess-1',
          branch_id: 'branch-1',
          operation_id: 'op-1',
          operation_type: 'expand_next',
          seed_node_id: 'ethereum:address:0xabc',
          seed_lineage_id: 'lineage-1',
          expansion_depth: 1,
          added_nodes: [
            {
              node_id: 'ethereum:address:0xabc',
              node_type: 'address',
              branch_id: 'branch-1',
              path_id: 'path-1',
              lineage_id: 'lineage-1',
              depth: 0,
              chain: 'ethereum',
              display_label: '0xabc',
              expandable_directions: ['next'],
            },
          ],
          added_edges: [
            {
              edge_id: 'edge-1',
              edge_type: 'transfer',
              source_node_id: 'ethereum:address:0xabc',
              target_node_id: 'ethereum:address:0xdef',
              branch_id: 'branch-1',
              path_id: 'path-1',
              direction: 'forward',
              value_fiat: 19.75,
            },
          ],
          removed_node_ids: [],
          updated_nodes: [],
          has_more: true,
          continuation_token: 'token-1',
          pagination: {
            page_size: 25,
            max_results: 100,
            has_more: true,
            next_token: 'token-1',
          },
          layout_hints: { suggested_layout: 'layered', anchor_node_ids: ['ethereum:address:0xabc'] },
          chain_context: { primary_chain: 'ethereum', chains_present: ['ethereum'] },
          asset_context: { assets_present: ['USDC'], canonical_asset_ids: ['usd-coin'] },
          data_sources: ['event_store'],
          ingest_pending: false,
          timestamp: '2026-04-09T12:00:00Z',
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );

    const response = await expandNode('sess-1', {
      seed_node_id: 'ethereum:address:0xabc',
      operation_type: 'expand_next',
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(response.added_nodes).toHaveLength(1);
    expect(response.added_edges).toHaveLength(1);
    expect(response.nodes).toEqual(response.added_nodes);
    expect(response.edges).toEqual(response.added_edges);
    expect(response.has_more).toBe(true);
    expect(response.continuation_token).toBe('token-1');
    expect(response.pagination).toEqual({
      page_size: 25,
      max_results: 100,
      has_more: true,
      next_token: 'token-1',
    });
    expect(response.data_sources).toEqual(['event_store']);
    expect(response.added_edges[0]).toEqual(
      expect.objectContaining({
        value_fiat: 19.75,
      }),
    );
  });

  it('surfaces HTTP status on snapshot save conflicts', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({ detail: 'Stale workspace snapshot revision.' }),
        {
          status: 409,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );

    await expect(
      saveSessionSnapshot('sess-1', {
        revision: 4,
        sessionId: 'sess-1',
        nodes: [],
        edges: [],
        positions: {},
      }),
    ).rejects.toBeInstanceOf(ApiError);

    await expect(
      saveSessionSnapshot('sess-1', {
        revision: 4,
        sessionId: 'sess-1',
        nodes: [],
        edges: [],
        positions: {},
      }),
    ).rejects.toMatchObject({ status: 409 });
  });
});
