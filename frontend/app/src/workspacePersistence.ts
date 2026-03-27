export interface SavedWorkspace {
  sessionId: string;
  snapshot: string;
  savedAt: string;
}

const WORKSPACE_STORAGE_KEY = 'jds.graph.workspace.v1';

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
      || typeof parsed.snapshot !== 'string'
      || typeof parsed.savedAt !== 'string'
    ) {
      return null;
    }
    return parsed as SavedWorkspace;
  } catch {
    return null;
  }
}

export function saveWorkspace(sessionId: string, snapshot: string): void {
  if (!canUseStorage()) return;
  const payload: SavedWorkspace = {
    sessionId,
    snapshot,
    savedAt: new Date().toISOString(),
  };
  window.localStorage.setItem(WORKSPACE_STORAGE_KEY, JSON.stringify(payload));
}

export function clearSavedWorkspace(): void {
  if (!canUseStorage()) return;
  window.localStorage.removeItem(WORKSPACE_STORAGE_KEY);
}
