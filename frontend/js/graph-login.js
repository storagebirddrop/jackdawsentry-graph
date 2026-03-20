(function () {
    if (Auth.isAuthenticated()) {
        window.location.href = '/app/';
        return;
    }

    var form = document.getElementById('login-form');
    if (!form) {
        return;
    }

    form.addEventListener('submit', async function (event) {
        event.preventDefault();

        var errorEl = document.getElementById('login-error');
        var btn = document.getElementById('login-btn');
        var btnText = document.getElementById('login-btn-text');
        var spinner = document.getElementById('login-spinner');
        var username = document.getElementById('username').value.trim();
        var password = document.getElementById('password').value;

        errorEl.classList.add('hidden');
        btn.disabled = true;
        btnText.textContent = 'Signing in...';
        spinner.classList.remove('hidden');

        try {
            await Auth.login(username, password);
            window.location.href = '/app/';
        } catch (err) {
            errorEl.textContent = err.message || 'Login failed';
            errorEl.classList.remove('hidden');
            btn.disabled = false;
            btnText.textContent = 'Sign in';
            spinner.classList.add('hidden');
        }
    });
})();
