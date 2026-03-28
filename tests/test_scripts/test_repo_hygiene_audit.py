from scripts.quality.repo_hygiene_audit import find_forbidden_tracked_files


def test_repo_hygiene_audit_allows_public_repo_files():
    findings = find_forbidden_tracked_files(
        [
            "README.md",
            "docs/assurance/security-authz-abuse-summary.md",
            "docs/drills/README.md",
            "docs/drills/runs/.gitkeep",
            "docs/drills/templates/DRILL_RUN_TEMPLATE.md",
            "src/api/auth.py",
        ]
    )

    assert findings == []


def test_repo_hygiene_audit_flags_internal_only_paths():
    findings = find_forbidden_tracked_files(
        [
            "tasks/memory.md",
            "artifacts/runtime/security_probe.json",
            "docs/drills/archive/2026-03/run.md",
            "docs/drills/runs/2026-03-28-security/records/NEXT_WAVE_HANDOFF.md",
            "docs/drills/DRILL_FRAMEWORK.md",
            "security_focused_pytest.log",
        ]
    )

    assert findings == [
        "artifacts/runtime/security_probe.json: raw artifacts should not be tracked in the public repo",
        "docs/drills/DRILL_FRAMEWORK.md: internal drill framework should not be tracked publicly",
        "docs/drills/archive/2026-03/run.md: archived drill evidence should remain out of the public repo",
        "docs/drills/runs/2026-03-28-security/records/NEXT_WAVE_HANDOFF.md: internal drill runs should remain out of the public repo",
        "security_focused_pytest.log: raw logs should not be tracked in the public repo",
        "tasks/memory.md: internal working notes should not be tracked in the public repo",
    ]
