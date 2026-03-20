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
