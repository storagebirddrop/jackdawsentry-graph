/**
 * App root — toggles between SessionStarter and InvestigationGraph.
 */

import { useCallback, useEffect, useState } from 'react';
import SessionStarter from './components/SessionStarter';
import InvestigationGraph from './components/InvestigationGraph';
import { useGraphStore } from './store/graphStore';

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const resetGraph = useGraphStore((state) => state.reset);

  const handleStartNewInvestigation = useCallback(() => {
    resetGraph();
    setSessionId(null);
  }, [resetGraph]);

  useEffect(() => {
    document.body.classList.add('graph-app-body');
    document.body.classList.add('graph-app-boot-complete');
    return () => {
      document.body.classList.remove('graph-app-body');
      document.body.classList.remove('graph-app-boot-complete');
    };
  }, []);

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
