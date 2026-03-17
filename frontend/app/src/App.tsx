/**
 * App root — toggles between SessionStarter and InvestigationGraph.
 */

import { useState } from 'react';
import SessionStarter from './components/SessionStarter';
import InvestigationGraph from './components/InvestigationGraph';

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(null);

  if (!sessionId) {
    return <SessionStarter onSessionCreated={setSessionId} />;
  }

  return <InvestigationGraph sessionId={sessionId} />;
}
