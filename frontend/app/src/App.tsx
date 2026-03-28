/**
 * App root — toggles between SessionStarter and InvestigationGraph.
 *
 * On boot, fetches /health to check whether the backend is running in
 * auth-disabled mode.  When auth_disabled=true the JWT check and login
 * redirect are skipped so local standalone instances need no credentials.
 */

import { useCallback, useEffect, useState } from 'react';
import SessionStarter from './components/SessionStarter';
import InvestigationGraph from './components/InvestigationGraph';
import { useGraphStore } from './store/graphStore';
import { getRecentSessions, getSession, isAuthenticated, redirectToLogin } from './api/client';
import type { RecentSessionSummary } from './types/graph';
import {
  clearSavedWorkspace,
  loadSavedWorkspace,
  saveWorkspace,
  saveSessionWorkspacePreferences,
} from './workspacePersistence';

type RestoreNotice = { tone: 'info' | 'error'; message: string } | null;

type ActiveSession = {
  sessionId: string;
  initialWorkspaceRevision: number;
  initialSavedAt: string | null;
  initialRestoreNotice: RestoreNotice;
};

export default function App() {
  const [activeSession, setActiveSession] = useState<ActiveSession | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [restoreCandidate, setRestoreCandidate] = useState<RecentSessionSummary | null>(null);
  const resetGraph = useGraphStore((state) => state.reset);
  const importSnapshot = useGraphStore((state) => state.importSnapshot);

  const handleStartNewInvestigation = useCallback(() => {
    clearSavedWorkspace();
    resetGraph();
    setActiveSession(null);
  }, [resetGraph]);

  const loadRestoreCandidate = useCallback(async () => {
    const savedWorkspace = loadSavedWorkspace();
    try {
      const response = await getRecentSessions(savedWorkspace ? 5 : 1);
      const recentItems = response.items ?? [];
      const hintedSession = savedWorkspace
        ? recentItems.find((item) => item.session_id === savedWorkspace.sessionId) ?? null
        : null;
      setRestoreCandidate(hintedSession ?? recentItems[0] ?? null);
    } catch (error) {
      console.error('Failed to load recent investigation sessions:', error);
      setRestoreCandidate(null);
    }
  }, []);

  const handleRestoreWorkspace = useCallback(() => {
    const targetSessionId = restoreCandidate?.session_id;
    if (!targetSessionId) return;

    void (async () => {
      try {
        const response = await getSession(targetSessionId);
        const restored = importSnapshot(JSON.stringify(response.workspace));
        if (!restored) {
          clearSavedWorkspace();
          window.alert('Failed to restore the saved investigation workspace.');
          void loadRestoreCandidate();
          return;
        }

        if (response.workspace.workspacePreferences) {
          saveSessionWorkspacePreferences(
            targetSessionId,
            response.workspace.workspacePreferences,
          );
        }

        setActiveSession({
          sessionId: targetSessionId,
          initialWorkspaceRevision: response.workspace.revision,
          initialSavedAt: response.snapshot_saved_at ?? null,
          initialRestoreNotice:
            response.restore_state === 'legacy_bootstrap'
              ? {
                  tone: 'info',
                  message:
                    'Restored a reduced session snapshot. This backend row only preserved the seed node and legacy node-state hints, not the full prior workspace canvas.',
                }
              : null,
        });
      } catch (error) {
        console.error('Failed to restore workspace from backend session snapshot:', error);
        clearSavedWorkspace();
        setRestoreCandidate(null);
        window.alert('Failed to restore the saved investigation workspace.');
        void loadRestoreCandidate();
      }
    })();
  }, [importSnapshot, loadRestoreCandidate, restoreCandidate]);

  useEffect(() => {
    if (!activeSession) return;
    saveWorkspace(activeSession.sessionId);
  }, [activeSession]);

  useEffect(() => {
    async function bootstrap() {
      // Check whether the backend is running with auth disabled.
      let authDisabled = false;
      try {
        const resp = await fetch('/health');
        if (resp.ok) {
          const data = (await resp.json()) as { auth_disabled?: boolean };
          authDisabled = data.auth_disabled === true;
        }
      } catch {
        // Health check failed — assume auth is required and fall through.
      }

      if (!authDisabled && !isAuthenticated()) {
        redirectToLogin();
        return;
      }

      setAuthChecked(true);
      document.body.classList.add('graph-app-body');
      document.body.classList.add('graph-app-boot-complete');
    }

    void bootstrap();

    return () => {
      document.body.classList.remove('graph-app-body');
      document.body.classList.remove('graph-app-boot-complete');
    };
  }, []);

  useEffect(() => {
    if (!authChecked || activeSession) return;
    void loadRestoreCandidate();
  }, [activeSession, authChecked, loadRestoreCandidate]);

  // Gate rendering until auth state is resolved.
  if (!authChecked) {
    return null;
  }

  if (!activeSession) {
    return (
      <SessionStarter
        onSessionCreated={setActiveSession}
        onRestoreWorkspace={handleRestoreWorkspace}
        restoreCandidate={restoreCandidate}
      />
    );
  }

  return (
    <InvestigationGraph
      key={activeSession.sessionId}
      sessionId={activeSession.sessionId}
      initialWorkspaceRevision={activeSession.initialWorkspaceRevision}
      initialSavedAt={activeSession.initialSavedAt}
      initialRestoreNotice={activeSession.initialRestoreNotice}
      onStartNewInvestigation={handleStartNewInvestigation}
    />
  );
}
