export interface SavedWorkspace {
  sessionId: string;
  savedAt: string;
  snapshot?: string;
}

export interface SessionWorkspacePreferences {
  selectedAssets: string[];
  pinnedAssetKeys: string[];
  assetCatalogScope: 'session' | 'visible';
  savedAt: string;
}

export interface SnapshotWorkspacePreferences {
  selectedAssets: string[];
  pinnedAssetKeys: string[];
  assetCatalogScope: 'session' | 'visible';
}

const WORKSPACE_STORAGE_KEY = 'jds.graph.workspace.v1';
const SESSION_PREFERENCES_STORAGE_KEY = 'jds.graph.session-preferences.v1';

function canUseStorage(): boolean {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
}

export function loadSavedWorkspace(): SavedWorkspace | null {
  if (!canUseStorage()) return null;
  const raw = window.localStorage.getItem(WORKSPACE_STORAGE_KEY);
  if (!raw) return null;

  try {
    const parsed = JSON.parse(raw) as Partial<SavedWorkspace>;
    if (
      typeof parsed.sessionId !== 'string'
      || typeof parsed.savedAt !== 'string'
    ) {
      return null;
    }
    return {
      sessionId: parsed.sessionId,
      savedAt: parsed.savedAt,
      snapshot: typeof parsed.snapshot === 'string' ? parsed.snapshot : undefined,
    };
  } catch {
    return null;
  }
}

export function saveWorkspace(sessionId: string, _snapshot?: string): void {
  if (!canUseStorage()) return;
  const payload: SavedWorkspace = {
    sessionId,
    savedAt: new Date().toISOString(),
  };
  window.localStorage.setItem(WORKSPACE_STORAGE_KEY, JSON.stringify(payload));
}

export function clearSavedWorkspace(): void {
  if (!canUseStorage()) return;
  window.localStorage.removeItem(WORKSPACE_STORAGE_KEY);
}

function loadSessionPreferencesStore(): Record<string, SessionWorkspacePreferences> {
  if (!canUseStorage()) return {};
  const raw = window.localStorage.getItem(SESSION_PREFERENCES_STORAGE_KEY);
  if (!raw) return {};

  try {
    const parsed = JSON.parse(raw) as Record<string, SessionWorkspacePreferences>;
    if (!parsed || typeof parsed !== 'object') return {};
    return parsed;
  } catch {
    return {};
  }
}

function saveSessionPreferencesStore(store: Record<string, SessionWorkspacePreferences>): void {
  if (!canUseStorage()) return;
  window.localStorage.setItem(SESSION_PREFERENCES_STORAGE_KEY, JSON.stringify(store));
}

export function loadSessionWorkspacePreferences(sessionId: string): SessionWorkspacePreferences | null {
  if (!sessionId) return null;
  const store = loadSessionPreferencesStore();
  const entry = store[sessionId];
  if (!entry) return null;

  if (
    !Array.isArray(entry.selectedAssets)
    || !Array.isArray(entry.pinnedAssetKeys)
    || (entry.assetCatalogScope !== 'session' && entry.assetCatalogScope !== 'visible')
  ) {
    return null;
  }

  return {
    selectedAssets: entry.selectedAssets.filter((value): value is string => typeof value === 'string'),
    pinnedAssetKeys: entry.pinnedAssetKeys.filter((value): value is string => typeof value === 'string'),
    assetCatalogScope: entry.assetCatalogScope,
    savedAt: typeof entry.savedAt === 'string' ? entry.savedAt : new Date().toISOString(),
  };
}

export function saveSessionWorkspacePreferences(
  sessionId: string,
  preferences: Omit<SessionWorkspacePreferences, 'savedAt'>,
): void {
  if (!canUseStorage() || !sessionId) return;
  const store = loadSessionPreferencesStore();
  store[sessionId] = {
    selectedAssets: preferences.selectedAssets,
    pinnedAssetKeys: preferences.pinnedAssetKeys,
    assetCatalogScope: preferences.assetCatalogScope,
    savedAt: new Date().toISOString(),
  };
  saveSessionPreferencesStore(store);
}

export function extractSnapshotWorkspacePreferences(
  snapshot: string,
): SnapshotWorkspacePreferences | null {
  try {
    const parsed = JSON.parse(snapshot) as {
      workspacePreferences?: Partial<SnapshotWorkspacePreferences> | null;
    };
    const prefs = parsed.workspacePreferences;
    if (!prefs) return null;
    if (
      !Array.isArray(prefs.selectedAssets)
      || !Array.isArray(prefs.pinnedAssetKeys)
      || (prefs.assetCatalogScope !== 'session' && prefs.assetCatalogScope !== 'visible')
    ) {
      return null;
    }
    return {
      selectedAssets: prefs.selectedAssets.filter((value): value is string => typeof value === 'string'),
      pinnedAssetKeys: prefs.pinnedAssetKeys.filter((value): value is string => typeof value === 'string'),
      assetCatalogScope: prefs.assetCatalogScope,
    };
  } catch {
    return null;
  }
}
