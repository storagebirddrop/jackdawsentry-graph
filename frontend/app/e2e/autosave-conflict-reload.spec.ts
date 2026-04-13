import { expect, test, type Page } from '@playwright/test';

import {
  ROOT_NODE_ID,
  ROOT_SELECTOR,
  makeAssetOptionsResponse,
  makeWorkspaceSnapshot,
} from './fixtures/graphWorkflow';
import { installMockGraphApi } from './support/mockGraphApi';

const AUTOSAVE_DEBOUNCE_MS = 320;

async function restoreSavedWorkspace(page: Page): Promise<void> {
  await page.goto('/app/');
  await expect(page.getByRole('button', { name: 'Restore Saved Workspace' })).toBeVisible();
  await page.getByRole('button', { name: 'Restore Saved Workspace' }).click();
  await expect(page.getByText('1 nodes · 0 edges')).toBeVisible();
}

async function selectRootNode(page: Page): Promise<void> {
  await page.getByTestId(`rf__node-${ROOT_NODE_ID}`).click();
}

async function expectSnapshotCountStable(
  page: Page,
  snapshotCount: () => number,
  expectedCount: number,
): Promise<void> {
  await page.waitForTimeout(AUTOSAVE_DEBOUNCE_MS + 180);
  expect(snapshotCount()).toBe(expectedCount);
}

test('pauses autosave on a stale revision conflict and reloads the saved version without overwriting it', async ({ page }) => {
  const initialWorkspace = makeWorkspaceSnapshot({ revision: 8 });
  const serverReloadWorkspace = makeWorkspaceSnapshot({ revision: 9 });

  const api = await installMockGraphApi(page, {
    serverWorkspace: initialWorkspace,
    assetOptionsResponse: makeAssetOptionsResponse(),
  });

  api.queueSnapshotConflictOnce({
    serverWorkspace: serverReloadWorkspace,
    snapshotSavedAt: '2026-04-13T10:09:00Z',
    message: 'API 409: stale workspace snapshot revision',
  });

  await restoreSavedWorkspace(page);
  await selectRootNode(page);

  await expect.poll(() => api.assetOptionsRequests.length).toBe(1);
  await expect(page.getByRole('radio', { name: 'Specific assets' })).toBeChecked();
  await expect(page.getByRole('checkbox', { name: 'USDC · Ethereum' })).toBeChecked();

  await page.getByRole('radio', { name: 'All assets' }).check();

  await expect.poll(() => api.snapshotRequests.length).toBe(1);
  expect(api.snapshotRequests[0]?.revision).toBe(8);

  await expect(page.getByText('Autosave paused')).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('Another tab or session saved a newer version');
  await expect(page.getByRole('button', { name: 'Load saved version' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save my version' })).toBeVisible();

  await page.getByRole('radio', { name: 'Specific assets' }).check();
  await page.getByRole('checkbox', { name: 'WETH · Ethereum' }).check();

  await expectSnapshotCountStable(page, () => api.snapshotRequests.length, 1);
  expect(api.getServerWorkspace().revision).toBe(9);
  expect(api.getServerWorkspace().nodeAssetScopes?.[ROOT_NODE_ID]).toEqual([ROOT_SELECTOR]);

  page.once('dialog', async (dialog) => {
    await dialog.accept();
  });
  await page.getByRole('button', { name: 'Load saved version' }).click();

  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByText('Autosave paused')).toHaveCount(0);

  await selectRootNode(page);
  await expect(page.getByRole('radio', { name: 'Specific assets' })).toBeChecked();
  await expect(page.getByRole('checkbox', { name: 'USDC · Ethereum' })).toBeChecked();
  await expect(page.getByRole('checkbox', { name: 'WETH · Ethereum' })).not.toBeChecked();

  await page.getByRole('radio', { name: 'All assets' }).check();

  await expect.poll(() => api.snapshotRequests.length).toBe(2);
  expect(api.snapshotRequests[1]?.revision).toBe(9);
  expect(api.getServerWorkspace().revision).toBe(10);
});
