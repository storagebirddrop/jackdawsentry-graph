// @vitest-environment jsdom

import { describe, expect, it } from 'vitest';

import { toRfEdge } from '../store/graphStore';
import type { InvestigationEdge } from './graph';
import { normalizeInvestigationEdge } from './graph';

function makeEdge(overrides: Partial<InvestigationEdge> = {}): InvestigationEdge {
  return {
    edge_id: 'edge-1',
    edge_type: 'transfer',
    source_node_id: 'ethereum:address:0xABCDEF',
    target_node_id: 'ethereum:address:0x123456',
    direction: 'forward',
    branch_id: 'branch-1',
    ...overrides,
  };
}

describe('graph contract normalization', () => {
  it('normalizes backend-canonical edge fiat values and EVM endpoint ids', () => {
    expect(
      normalizeInvestigationEdge(
        makeEdge({
          value_fiat: 125.5,
        }),
      ),
    ).toEqual(
      expect.objectContaining({
        source_node_id: 'ethereum:address:0xabcdef',
        target_node_id: 'ethereum:address:0x123456',
        value_fiat: 125.5,
        fiat_value_usd: 125.5,
      }),
    );
  });

  it('lifts the legacy fiat alias into the canonical edge field', () => {
    expect(
      normalizeInvestigationEdge(
        makeEdge({
          source_node_id: 'solana:address:So11111111111111111111111111111111111111112',
          target_node_id: 'solana:address:Target111111111111111111111111111111111111',
          fiat_value_usd: 42,
        }),
      ),
    ).toEqual(
      expect.objectContaining({
        source_node_id: 'solana:address:So11111111111111111111111111111111111111112',
        target_node_id: 'solana:address:Target111111111111111111111111111111111111',
        value_fiat: 42,
        fiat_value_usd: 42,
      }),
    );
  });

  it('treats bridge_source and bridge_dest as the animated bridge edge contract', () => {
    expect(
      toRfEdge(
        makeEdge({
          edge_id: 'edge-bridge-source',
          edge_type: 'bridge_source',
        }),
      ).animated,
    ).toBe(true);

    expect(
      toRfEdge(
        makeEdge({
          edge_id: 'edge-bridge-dest',
          edge_type: 'bridge_dest',
        }),
      ).animated,
    ).toBe(true);
  });
});
