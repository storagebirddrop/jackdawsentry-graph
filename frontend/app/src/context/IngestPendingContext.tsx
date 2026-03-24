/**
 * IngestPendingContext — tracks nodes whose expansion returned ingest_pending=true.
 *
 * AddressNode reads this context to show a "Fetching data…" indicator while
 * background ingestion is in progress.  InvestigationGraph provides the value.
 */

import { createContext, useContext } from 'react';

interface IngestPendingContextValue {
  /** Set of node IDs whose background ingest is still pending/running. */
  pendingNodeIds: ReadonlySet<string>;
}

export const IngestPendingContext = createContext<IngestPendingContextValue>({
  pendingNodeIds: new Set(),
});

export function useIngestPending(): IngestPendingContextValue {
  return useContext(IngestPendingContext);
}
