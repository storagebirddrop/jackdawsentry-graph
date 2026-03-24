# Semgrep Security Scanning

## Overview

Semgrep is configured to provide **visible, actionable security feedback** during development and in pull requests.

## Local Development

### Installation

```bash
pip install semgrep
```

### Run Security Scan

```bash
# Scan all source code
semgrep --config .semgrep.yml src/

# Scan with verbose output
semgrep --config .semgrep.yml --verbose src/

# Output JSON format
semgrep --config .semgrep.yml --json --output results.json src/

# Scan specific files
semgrep --config .semgrep.yml src/api/auth.py src/collectors/
```

### What Gets Scanned

- **Security vulnerabilities**: SQL injection, command injection, path traversal
- **Authentication issues**: Hardcoded secrets, weak auth patterns
- **Input validation**: Unvalidated user input
- **Database security**: Raw SQL, missing parameters
- **Information disclosure**: Error handling that leaks sensitive data
- **Async issues**: Missing awaits, blocking calls

### Excluded Paths

- `frontend/app/node_modules/`
- `tests/`
- `migrations/`
- `__pycache__/`

## CI/CD Integration

### Pull Request Scanning

Every PR to `main` automatically runs Semgrep and creates a **PR comment** with:
- 🔴 Error count (critical issues)
- 🟡 Warning count (important issues)
- 🔵 Info count (informational findings)
- Detailed findings with file locations and fix suggestions

### External Scan

The original external Semgrep workflow (`.github/workflows/semgrep.yml`) continues to run as a **backup scan** for defense-in-depth.

## Configuration

The `.semgrep.yml` file uses official Semgrep rulesets:
- `p/security-audit` - Core security patterns
- `p/secrets` - Hardcoded credentials detection
- `p/python` - Python-specific security rules
- `p/javascript` - JavaScript security patterns
- `p/typescript` - TypeScript security patterns

## Customizing Rules

To add custom rules, edit `.semgrep.yml`:

```yaml
rules:
  - id: custom-rule
    pattern: your_pattern_here
    message: Custom security check
    severity: WARNING
    languages: [python]
```

## Fixing Findings

1. **Review the finding** in the PR comment or local output
2. **Understand the vulnerability** - Semgrep provides clear messages
3. **Apply the fix** - Usually straightforward (parameterize queries, validate input, etc.)
4. **Re-run locally** to verify the fix: `semgrep --config .semgrep.yml src/`

## Benefits Over Previous Tools

✅ **Visible** - You see findings in PRs where you work
✅ **Accurate** - Low false positive rate with official rulesets
✅ **Controlled** - No auto-fixes without your consent
✅ **Actionable** - Clear guidance on what to fix
✅ **No overreach** - Stays focused on security, doesn't make unwanted changes
