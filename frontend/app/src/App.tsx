/**
 * App root — toggles between SessionStarter and InvestigationGraph.
 */

import { useCallback, useEffect, useState } from 'react';
import SessionStarter from './components/SessionStarter';
import InvestigationGraph from './components/InvestigationGraph';
import { isAuthenticated, redirectToLogin } from './api/client';
import { useGraphStore } from './store/graphStore';

type AuthState = 'redirecting' | 'ready';

export default function App() {
  const [authState] = useState<AuthState>(() => (
    isAuthenticated() ? 'ready' : 'redirecting'
  ));
  const [sessionId, setSessionId] = useState<string | null>(null);
  const resetGraph = useGraphStore((state) => state.reset);

  const handleStartNewInvestigation = useCallback(() => {
    resetGraph();
    setSessionId(null);
  }, [resetGraph]);

  useEffect(() => {
    if (authState === 'redirecting') {
      redirectToLogin();
    }
  }, [authState]);

  useEffect(() => {
    document.body.classList.add('graph-app-body');
    return () => {
      document.body.classList.remove('graph-app-body');
      document.body.classList.remove('graph-app-boot-complete');
    };
  }, []);

  useEffect(() => {
    document.body.classList.toggle('graph-app-boot-complete', authState === 'ready');
  }, [authState]);

  if (authState !== 'ready') {
    const isRedirecting = authState === 'redirecting';

    return (
      <div className="app-auth-shell" role="status" aria-live="polite">
        <div className="app-auth-card">
          <div className="app-auth-spinner" aria-hidden="true"></div>
          <div className="app-auth-copy">
            <p className="app-auth-eyebrow">Jackdaw Sentry Graph</p>
            <h1 className="app-auth-title">
              {isRedirecting ? 'Returning to sign in' : 'Opening investigation graph'}
            </h1>
            <p className="app-auth-message">
              {isRedirecting
                ? 'Your analyst session needs to be restored before the graph canvas can open.'
                : 'Checking your session and loading the investigation workspace.'}
            </p>
          </div>
        </div>
      </div>
    );
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
