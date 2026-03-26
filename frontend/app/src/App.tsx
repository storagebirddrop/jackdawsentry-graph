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
import { isAuthenticated, redirectToLogin } from './api/client';

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const resetGraph = useGraphStore((state) => state.reset);

  const handleStartNewInvestigation = useCallback(() => {
    resetGraph();
    setSessionId(null);
  }, [resetGraph]);

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

  // Gate rendering until auth state is resolved.
  if (!authChecked) {
    return null;
  }

  if (!sessionId) {
    return <SessionStarter onSessionCreated={setSessionId} />;
  }

  return (
    <InvestigationGraph
      key={sessionId}
      sessionId={sessionId}
      onStartNewInvestigation={handleStartNewInvestigation}
    />
  );
}
