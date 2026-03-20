(function () {
    var submitting = false;
    var APP_PATH = '/app/';
    var body = document.body;

    function updateStatus(title, message) {
        var titleEl = document.getElementById('login-status-title');
        var messageEl = document.getElementById('login-status-message');
        if (titleEl && title) {
            titleEl.textContent = title;
        }
        if (messageEl && message) {
            messageEl.textContent = message;
        }
    }

    function markReady() {
        if (!body) {
            return;
        }
        body.classList.remove('is-auth-pending');
        body.classList.remove('is-transitioning');
        body.classList.add('is-auth-ready');
    }

    function markTransitioning() {
        if (!body) {
            return;
        }
        body.classList.remove('is-auth-pending');
        body.classList.remove('is-auth-ready');
        body.classList.add('is-transitioning');
    }

    function getShell() {
        return document.querySelector('.graph-login-shell');
    }

    function setSubmittingState(isSubmitting) {
        var shell = getShell();
        var form = document.getElementById('login-form');
        var btn = document.getElementById('login-btn');
        var btnText = document.getElementById('login-btn-text');
        var spinner = document.getElementById('login-spinner');
        var inputs = form ? form.querySelectorAll('input') : [];

        submitting = isSubmitting;
        if (shell) {
            shell.classList.toggle('is-authenticating', isSubmitting);
        }
        if (isSubmitting) {
            updateStatus(
                'Signing in',
                'Validating your credentials and preparing the investigation graph.',
            );
            markTransitioning();
        } else {
            markReady();
        }
        if (btn) {
            btn.disabled = isSubmitting;
            btn.setAttribute('aria-busy', String(isSubmitting));
        }
        if (btnText) {
            btnText.textContent = isSubmitting ? 'Signing in...' : 'Sign in';
        }
        if (spinner) {
            spinner.classList.toggle('hidden', !isSubmitting);
        }

        inputs.forEach(function (input) {
            input.disabled = isSubmitting;
        });
    }

    function redirectToApp() {
        var shell = getShell();
        updateStatus(
            'Opening investigation graph',
            'Restoring your analyst workspace and moving into the graph canvas.',
        );
        markTransitioning();
        if (shell) {
            shell.classList.add('is-authenticating');
        }
        window.location.replace(APP_PATH);
    }

    if (Auth.isAuthenticated()) {
        redirectToApp();
        return;
    }

    updateStatus(
        'Restoring session',
        'Checking your analyst session before opening the investigation graph.',
    );
    markReady();

    var form = document.getElementById('login-form');
    if (!form) {
        return;
    }

    form.addEventListener('submit', async function (event) {
        event.preventDefault();
        if (submitting) {
            return;
        }

        var errorEl = document.getElementById('login-error');
        var username = document.getElementById('username').value.trim();
        var password = document.getElementById('password').value;

        errorEl.classList.add('hidden');
        setSubmittingState(true);

        try {
            await Auth.login(username, password);
            redirectToApp();
        } catch (err) {
            errorEl.textContent = err.message || 'Login failed';
            errorEl.classList.remove('hidden');
            setSubmittingState(false);
        }
    });
})();
