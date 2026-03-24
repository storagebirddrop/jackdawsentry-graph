/**
 * App root — toggles between SessionStarter and InvestigationGraph.
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
    if (!isAuthenticated()) {
      redirectToLogin();
      return; // Don't set up UI or mark auth as checked
    }
    setAuthChecked(true);
    document.body.classList.add('graph-app-body');
    document.body.classList.add('graph-app-boot-complete');
    return () => {
      document.body.classList.remove('graph-app-body');
      document.body.classList.remove('graph-app-boot-complete');
    };
  }, []);

  // Gate rendering until auth is confirmed
  if (!authChecked) {
    return null; // or a loading spinner
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
