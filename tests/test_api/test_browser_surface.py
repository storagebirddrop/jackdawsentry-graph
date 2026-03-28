from pathlib import Path


def test_graph_login_page_uses_only_local_assets():
    login_html = Path("frontend/graph-login.html").read_text(encoding="utf-8")

    assert "cdn.tailwindcss.com" not in login_html
    assert "<script>" not in login_html
    assert 'src="/js/auth.js"' in login_html
    assert 'src="/js/graph-login.js"' in login_html
    assert 'href="/css/graph-login.css"' in login_html


def test_graph_auth_uses_session_storage_not_local_storage():
    auth_js = Path("frontend/js/auth.js").read_text(encoding="utf-8")
    client_ts = Path("frontend/app/src/api/client.ts").read_text(encoding="utf-8")

    assert "sessionStorage" in auth_js
    assert "localStorage.getItem('access_token')" not in client_ts
    assert "sessionStorage.getItem('jds_token')" in client_ts


def test_restore_discovery_uses_backend_recent_sessions_not_local_storage_gate():
    app_ts = Path("frontend/app/src/App.tsx").read_text(encoding="utf-8")
    starter_ts = Path("frontend/app/src/components/SessionStarter.tsx").read_text(encoding="utf-8")
    client_ts = Path("frontend/app/src/api/client.ts").read_text(encoding="utf-8")

    assert "getRecentSessions" in app_ts
    assert "/graph/sessions/recent" in client_ts
    assert "loadSavedWorkspace" not in starter_ts
    assert "loadSavedWorkspace" not in app_ts
    assert "setRestoreCandidate(response.items?.[0] ?? null);" in app_ts


def test_restore_state_is_surfaced_to_investigator():
    app_ts = Path("frontend/app/src/App.tsx").read_text(encoding="utf-8")

    assert "legacy_bootstrap" in app_ts
    assert "Restored a reduced session snapshot" in app_ts


def test_autosave_uses_snapshot_revision_guardrails():
    graph_ts = Path("frontend/app/src/components/InvestigationGraph.tsx").read_text(encoding="utf-8")
    models_py = Path("src/trace_compiler/models.py").read_text(encoding="utf-8")
    router_py = Path("src/api/routers/graph.py").read_text(encoding="utf-8")

    assert "revision: int = 0" in models_py
    assert "snapshotPayload.revision = nextRevision" in graph_ts
    assert "Stale workspace snapshot revision" in router_py


def test_active_bridge_inspector_uses_mounted_bridge_hop_poller():
    graph_ts = Path("frontend/app/src/components/InvestigationGraph.tsx").read_text(encoding="utf-8")
    inspector_ts = Path("frontend/app/src/components/GraphInspectorPanel.tsx").read_text(encoding="utf-8")
    store_ts = Path("frontend/app/src/store/graphStore.ts").read_text(encoding="utf-8")
    hook_ts = Path("frontend/app/src/hooks/useBridgeHopPoller.ts").read_text(encoding="utf-8")

    assert "useBridgeHopPoller" in graph_ts
    assert "bridgeStatusRefresh={bridgeStatusRefresh}" in graph_ts
    assert "updateBridgeHopStatus" in store_ts
    assert "Polling every 30s" in inspector_ts
    assert "getBridgeHopStatus" in hook_ts
    assert "setTimeout(() => void poll(), POLL_INTERVAL_MS)" in hook_ts


def test_pending_bridge_wording_uses_correlation_confidence():
    inspector_ts = Path("frontend/app/src/components/GraphInspectorPanel.tsx").read_text(encoding="utf-8")
    node_ts = Path("frontend/app/src/components/nodes/BridgeHopNode.tsx").read_text(encoding="utf-8")

    assert "const unresolvedCorrelation =" in inspector_ts
    assert "Correlation confidence" in inspector_ts
    assert "correlation confidence" in node_ts


def test_ingest_not_found_does_not_stay_in_fetching_state():
    graph_ts = Path("frontend/app/src/components/InvestigationGraph.tsx").read_text(encoding="utf-8")
    hook_ts = Path("frontend/app/src/hooks/useIngestPoller.ts").read_text(encoding="utf-8")
    poller_ts = Path("frontend/app/src/components/IngestPoller.tsx").read_text(encoding="utf-8")

    assert "status becomes 'not_found'   → onUnavailable(nodeId) is called" in hook_ts
    assert "if (status.status === 'not_found')" in hook_ts
    assert "'pending' | 'running' → keep polling" in hook_ts
    assert "onUnavailable" in poller_ts
    assert "Background data fetch is no longer queued for this address." in graph_ts


def test_ingest_timeout_notice_is_persistent_and_scopes_uncertainty():
    graph_ts = Path("frontend/app/src/components/InvestigationGraph.tsx").read_text(encoding="utf-8")

    assert "autoDismiss: options?.autoDismiss ?? true" in graph_ts
    assert "if (!notice?.autoDismiss) return;" in graph_ts
    assert "Current results may be incomplete" in graph_ts
    assert "additional ingest may not be available in this runtime" in graph_ts
    assert "{ autoDismiss: false }" in graph_ts


def test_dead_bridge_drawer_path_is_retired():
    assert not Path("frontend/app/src/components/BridgeHopDrawer.tsx").exists()
