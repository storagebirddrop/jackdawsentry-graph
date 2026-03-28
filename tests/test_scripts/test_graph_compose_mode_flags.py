from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "docker-compose.graph.yml"


def test_graph_api_mode_flags_are_not_shell_interpolated():
    text = COMPOSE_FILE.read_text(encoding="utf-8")

    assert "env_file:\n      - ./.env" in text
    assert "${TRUST_PROXY_HEADERS:-false}" not in text
    assert "${EXPOSE_API_DOCS:-false}" not in text
    assert "${ENABLE_LEGACY_GRAPH_ENDPOINTS:-false}" not in text
    assert "${EXPOSE_METRICS:-false}" not in text
    assert "${RATE_LIMIT_ENABLED:-true}" not in text
    assert "${DUAL_WRITE_RAW_EVENT_STORE:-false}" not in text
    assert "${AUTO_BACKFILL_RAW_EVENT_STORE:-false}" not in text
