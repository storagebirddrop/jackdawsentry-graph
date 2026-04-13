import { expect, test, type Page } from '@playwright/test';

import {
  ROOT_NODE_ID,
  ROOT_SELECTOR,
  SECONDARY_SELECTOR,
  makeAssetOptionsResponse,
  makePreviewResponse,
  makeWorkspaceSnapshot,
} from './fixtures/graphWorkflow';
import { installMockGraphApi } from './support/mockGraphApi';

async function restoreSavedWorkspace(page: Page): Promise<void> {
  await page.goto('/app/');
  await expect(page.getByRole('button', { name: 'Restore Saved Workspace' })).toBeVisible();
  await page.getByRole('button', { name: 'Restore Saved Workspace' }).click();
  await expect(page.getByText('1 nodes · 0 edges')).toBeVisible();
}

async function selectRootNode(page: Page): Promise<void> {
  await page.getByTestId(`rf__node-${ROOT_NODE_ID}`).click();
}

test('restores asset scope, previews a scoped expansion, and applies a subset', async ({ page }) => {
  const api = await installMockGraphApi(page, {
    serverWorkspace: makeWorkspaceSnapshot(),
    assetOptionsResponse: makeAssetOptionsResponse(),
    expandResponse: makePreviewResponse(),
  });

  await restoreSavedWorkspace(page);
  await selectRootNode(page);

  await expect.poll(() => api.assetOptionsRequests.length).toBe(1);
  await expect(page.getByRole('radio', { name: 'Specific assets' })).toBeChecked();
  await expect(page.getByRole('checkbox', { name: 'USDC · Ethereum' })).toBeChecked();

  await page.getByLabel('Search assets').fill('weth');
  await expect(page.getByRole('checkbox', { name: 'WETH · Ethereum' })).toBeVisible();
  await page.getByRole('checkbox', { name: 'WETH · Ethereum' }).check();
  await expect(page.getByText('1 selected outside current filter')).toBeVisible();

  await page.getByRole('button', { name: 'Filter & Preview ▼' }).click();
  await page.getByRole('button', { name: 'Preview next' }).click();

  await expect.poll(() => api.expandRequests.length).toBe(1);
  expect(api.expandRequests[0]).toMatchObject({
    operation_type: 'expand_next',
    seed_node_id: ROOT_NODE_ID,
    options: {
      asset_selectors: [ROOT_SELECTOR, SECONDARY_SELECTOR],
      max_results: 25,
    },
  });

  await expect(page.getByRole('button', { name: 'Apply selected (2)' })).toBeVisible();
  await expect(page.getByText('1 nodes · 0 edges')).toBeVisible();

  await page.getByRole('checkbox', { name: /0xpreviewb/ }).uncheck();
  await expect(page.getByRole('button', { name: 'Apply selected (1)' })).toBeVisible();
  await page.getByRole('button', { name: 'Apply selected (1)' }).click();

  await expect(page.getByText('2 nodes · 1 edges')).toBeVisible();
  await expect(page.getByRole('button', { name: /^Apply selected/ })).toHaveCount(0);
});
