import type { Page, Route } from '@playwright/test';

import type {
  AssetOptionsRequest,
  AssetOptionsResponse,
  ExpandRequest,
  ExpansionResponseV2,
  InvestigationSessionResponse,
  RecentSessionSummary,
  WorkspaceSnapshotV1,
} from '../../src/types/graph';
import {
  makeAssetOptionsResponse,
  makeRestoreCandidate,
  makeSessionResponse,
  SESSION_ID,
} from '../fixtures/graphWorkflow';

type ExpandResponseFactory =
  | ExpansionResponseV2
  | ((request: ExpandRequest) => ExpansionResponseV2);

type AssetOptionsResponseFactory =
  | AssetOptionsResponse
  | ((request: AssetOptionsRequest) => AssetOptionsResponse);

interface SnapshotConflictPlan {
  remaining: number;
  message: string;
  serverWorkspace: WorkspaceSnapshotV1;
  snapshotSavedAt: string | null;
}

export interface MockGraphApiOptions {
  sessionId?: string;
  serverWorkspace: WorkspaceSnapshotV1;
  snapshotSavedAt?: string | null;
  recentSessions?: RecentSessionSummary[];
  assetOptionsResponse?: AssetOptionsResponseFactory;
  expandResponse?: ExpandResponseFactory;
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function parseJsonBody<T>(route: Route): T {
  const body = route.request().postData() ?? '{}';
  return JSON.parse(body) as T;
}

function savedAtForRevision(revision: number): string {
  const minutes = String(revision).padStart(2, '0');
  return `2026-04-13T10:${minutes}:00Z`;
}

export class MockGraphApi {
  readonly sessionId: string;
  readonly expandRequests: ExpandRequest[] = [];
  readonly snapshotRequests: WorkspaceSnapshotV1[] = [];
  readonly assetOptionsRequests: AssetOptionsRequest[] = [];
  readonly restoreRequests: string[] = [];

  private readonly page: Page;
  private readonly recentSessions: RecentSessionSummary[];
  private readonly assetOptionsResponse: AssetOptionsResponseFactory;
  private readonly expandResponse: ExpandResponseFactory | null;
  private installed = false;
  private snapshotCounter = 0;
  private snapshotConflictPlan: SnapshotConflictPlan | null = null;
  private snapshotSavedAt: string | null;
  private serverWorkspace: WorkspaceSnapshotV1;

  constructor(page: Page, options: MockGraphApiOptions) {
    this.page = page;
    this.sessionId = options.sessionId ?? SESSION_ID;
    this.serverWorkspace = clone(options.serverWorkspace);
    this.snapshotSavedAt = options.snapshotSavedAt ?? savedAtForRevision(this.serverWorkspace.revision);
    this.recentSessions = clone(options.recentSessions ?? [makeRestoreCandidate()]);
    this.assetOptionsResponse = options.assetOptionsResponse ?? makeAssetOptionsResponse();
    this.expandResponse = options.expandResponse ?? null;
  }

  async install(): Promise<this> {
    if (this.installed) return this;
    this.installed = true;

    await this.page.route('**/health', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ auth_disabled: true }),
      });
    });

    await this.page.route('**/api/v1/graph/sessions/recent?**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: clone(this.recentSessions) }),
      });
    });

    await this.page.route(`**/api/v1/graph/sessions/${this.sessionId}/asset-options`, async (route) => {
      const request = parseJsonBody<AssetOptionsRequest>(route);
      this.assetOptionsRequests.push(clone(request));
      const response = typeof this.assetOptionsResponse === 'function'
        ? this.assetOptionsResponse(request)
        : this.assetOptionsResponse;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(clone(response)),
      });
    });

    await this.page.route(`**/api/v1/graph/sessions/${this.sessionId}/expand`, async (route) => {
      const request = parseJsonBody<ExpandRequest>(route);
      this.expandRequests.push(clone(request));
      const response = this.expandResponse
        ? (typeof this.expandResponse === 'function'
          ? this.expandResponse(request)
          : this.expandResponse)
        : {
            session_id: this.sessionId,
            branch_id: 'branch-1',
            expansion_depth: 1,
            operation_id: 'op-empty',
            operation_type: request.operation_type,
            seed_node_id: request.seed_node_id,
            seed_lineage_id: request.seed_lineage_id ?? null,
            added_nodes: [],
            added_edges: [],
            updated_nodes: [],
            removed_node_ids: [],
            has_more: false,
            continuation_token: null,
            layout_hints: { suggested_layout: 'layered' },
            chain_context: { primary_chain: 'ethereum', chains_present: ['ethereum'] },
            pagination: { page_size: 25, max_results: 100, has_more: false, next_token: null },
          } satisfies ExpansionResponseV2;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(clone(response)),
      });
    });

    await this.page.route(`**/api/v1/graph/sessions/${this.sessionId}/snapshot`, async (route) => {
      const request = parseJsonBody<WorkspaceSnapshotV1>(route);
      this.snapshotRequests.push(clone(request));

      if (this.snapshotConflictPlan && this.snapshotConflictPlan.remaining > 0) {
        this.snapshotConflictPlan.remaining -= 1;
        this.serverWorkspace = clone(this.snapshotConflictPlan.serverWorkspace);
        this.snapshotSavedAt = this.snapshotConflictPlan.snapshotSavedAt;
        await route.fulfill({
          status: 409,
          contentType: 'text/plain',
          body: this.snapshotConflictPlan.message,
        });
        return;
      }

      if (request.revision !== this.serverWorkspace.revision) {
        await route.fulfill({
          status: 409,
          contentType: 'text/plain',
          body: 'stale workspace snapshot revision',
        });
        return;
      }

      this.snapshotCounter += 1;
      const nextRevision = this.serverWorkspace.revision + 1;
      this.serverWorkspace = {
        ...clone(request),
        revision: nextRevision,
      };
      this.snapshotSavedAt = savedAtForRevision(nextRevision);

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          snapshot_id: `snap-${this.snapshotCounter}`,
          saved_at: this.snapshotSavedAt,
          revision: nextRevision,
        }),
      });
    });

    await this.page.route(`**/api/v1/graph/sessions/${this.sessionId}`, async (route) => {
      this.restoreRequests.push(route.request().url());
      const response: InvestigationSessionResponse = makeSessionResponse(
        this.serverWorkspace,
        { snapshotSavedAt: this.snapshotSavedAt },
      );
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(response),
      });
    });

    return this;
  }

  queueSnapshotConflictOnce(options: {
    serverWorkspace: WorkspaceSnapshotV1;
    snapshotSavedAt?: string | null;
    message?: string;
  }): void {
    this.snapshotConflictPlan = {
      remaining: 1,
      message: options.message ?? 'stale workspace snapshot revision',
      serverWorkspace: clone(options.serverWorkspace),
      snapshotSavedAt: options.snapshotSavedAt ?? savedAtForRevision(options.serverWorkspace.revision),
    };
  }

  getServerWorkspace(): WorkspaceSnapshotV1 {
    return clone(this.serverWorkspace);
  }

  getSnapshotSavedAt(): string | null {
    return this.snapshotSavedAt;
  }
}

export async function installMockGraphApi(
  page: Page,
  options: MockGraphApiOptions,
): Promise<MockGraphApi> {
  const api = new MockGraphApi(page, options);
  await api.install();
  return api;
}
