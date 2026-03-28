/**
 * IngestPoller — render-null component that drives useIngestPoller for one node.
 *
 * Render-null is the React-idiomatic way to run a hook for each item in a
 * dynamic list without violating the rules-of-hooks (hooks cannot be called
 * inside loops or conditionals).  InvestigationGraph renders one <IngestPoller>
 * per node whose expansion returned ingest_pending=true.
 */

import { useIngestPoller } from '../hooks/useIngestPoller';

interface Props {
  sessionId: string;
  nodeId: string;
  address: string;
  chain: string;
  onComplete: (nodeId: string) => void;
  onUnavailable: (nodeId: string) => void;
  onTimeout: (nodeId: string) => void;
}

export default function IngestPoller({
  sessionId,
  nodeId,
  address,
  chain,
  onComplete,
  onUnavailable,
  onTimeout,
}: Props): null {
  useIngestPoller(sessionId, nodeId, address, chain, onComplete, onUnavailable, onTimeout);
  return null;
}
