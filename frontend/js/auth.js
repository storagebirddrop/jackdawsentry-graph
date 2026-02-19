/**
 * Jackdaw Sentry — Authentication Module
 * JWT token management, authenticated fetch, login/logout flows
 */

const Auth = (function () {
    const TOKEN_KEY = 'jds_token';
    const USER_KEY = 'jds_user';
    const LOGIN_PATH = '/login';

    /** Store token + user after successful login */
    function setSession(tokenResponse) {
        localStorage.setItem(TOKEN_KEY, tokenResponse.access_token);
        if (tokenResponse.user) {
            localStorage.setItem(USER_KEY, JSON.stringify(tokenResponse.user));
        }
    }

    /** Get stored JWT or null */
    function getToken() {
        return localStorage.getItem(TOKEN_KEY);
    }

    /** Get stored user object or null */
    function getUser() {
        try {
            const raw = localStorage.getItem(USER_KEY);
            return raw ? JSON.parse(raw) : null;
        } catch (_) {
            return null;
        }
    }

    /** True when a token exists (does NOT verify expiry server-side) */
    function isAuthenticated() {
        return !!getToken();
    }

    /** Clear session and redirect to login */
    function logout() {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
        window.location.href = LOGIN_PATH;
    }

    /** Redirect to login if not authenticated (call on every protected page) */
    function requireAuth() {
        if (!isAuthenticated()) {
            window.location.href = LOGIN_PATH;
        }
    }

    /** Show a toast notification (positioned below topbar) */
    function showToast(message, type) {
        type = type || 'info';
        var colorMap = {
            success: 'bg-emerald-600',
            error:   'bg-rose-600',
            warning: 'bg-amber-600',
            info:    'bg-blue-600',
        };
        var el = document.createElement('div');
        el.className =
            'fixed top-20 right-4 px-4 py-3 rounded-lg shadow-lg z-50 text-white text-sm ' +
            (colorMap[type] || colorMap.info);
        el.textContent = message;
        document.body.appendChild(el);
        setTimeout(function () { el.remove(); }, 4000);
    }

    /**
     * Wrapper around fetch() that injects Authorization header.
     * Automatically redirects to login on 401.
     * Shows toast on 403.
     * Retries once on 5xx.
     * Returns the Response object.
     */
    async function fetchWithAuth(url, opts = {}) {
        const token = getToken();
        const headers = Object.assign({}, opts.headers || {});
        if (token) {
            headers['Authorization'] = 'Bearer ' + token;
        }
        if (!headers['Content-Type'] && opts.body && typeof opts.body === 'string') {
            headers['Content-Type'] = 'application/json';
        }
        opts.headers = headers;

        const response = await fetch(url, opts);

        if (response.status === 401) {
            logout();
            throw new Error('Session expired');
        }

        if (response.status === 403) {
            showToast('Access denied — insufficient permissions', 'error');
            throw new Error('Forbidden');
        }

        // Retry once on 5xx
        if (response.status >= 500 && !opts._retried) {
            opts._retried = true;
            return fetchWithAuth(url, opts);
        }

        return response;
    }

    /**
     * Convenience: fetchWithAuth + parse JSON.
     * Returns parsed body or null on non-2xx.
     */
    async function fetchJSON(url, opts = {}) {
        const response = await fetchWithAuth(url, opts);
        if (!response.ok) return null;
        return response.json();
    }

    /**
     * POST /api/v1/auth/login
     * Returns token response or throws.
     */
    async function login(username, password) {
        const response = await fetch('/api/v1/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || 'Login failed');
        }

        const data = await response.json();
        setSession(data);
        return data;
    }

    return {
        getToken,
        getUser,
        isAuthenticated,
        requireAuth,
        login,
        logout,
        setSession,
        fetchWithAuth,
        fetchJSON,
        showToast
    };
})();
