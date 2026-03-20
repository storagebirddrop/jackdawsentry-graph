(function () {
    var submitting = false;
    var APP_PATH = '/app/';

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
        if (shell) {
            shell.classList.add('is-authenticating');
        }
        window.location.replace(APP_PATH);
    }

    if (Auth.isAuthenticated()) {
        redirectToApp();
        return;
    }

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
