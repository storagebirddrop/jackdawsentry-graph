/**
 * App root — toggles between SessionStarter and InvestigationGraph.
 */

import { useEffect, useState } from 'react';
import SessionStarter from './components/SessionStarter';
import InvestigationGraph from './components/InvestigationGraph';
import { isAuthenticated, redirectToLogin } from './api/client';

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(null);

  useEffect(() => {
    if (!isAuthenticated()) {
      redirectToLogin();
    }
  }, []);

  if (!sessionId) {
    return <SessionStarter onSessionCreated={setSessionId} />;
  }

  return <InvestigationGraph sessionId={sessionId} />;
}
