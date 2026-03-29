#!/usr/bin/env python3
"""Fail when the reviewed release path regresses to mutable provenance inputs."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def main() -> int:
    findings: list[str] = []

    compose = _read("docker-compose.graph.yml")
    dockerfile = _read("docker/Dockerfile")
    nginx_dockerfile = _read("docker/graph-nginx.Dockerfile")
    security_workflow = _read(".github/workflows/security.yml")

    if "./frontend:/usr/share/nginx/html" in compose:
        findings.append("graph-nginx still bind-mounts ./frontend into the served release path")

    for token in [
        "neo4j:5.14-community@sha256:",
        "postgres:15-alpine@sha256:",
        "redis:7-alpine@sha256:",
    ]:
        if token not in compose:
            findings.append(f"docker-compose.graph.yml is missing pinned runtime image token {token}")

    if "dockerfile: docker/graph-nginx.Dockerfile" not in compose:
        findings.append("graph-nginx is not built from docker/graph-nginx.Dockerfile")

    if "requirements.release.txt" not in dockerfile:
        findings.append("docker/Dockerfile is not installing from requirements.release.txt")

    for token in [
        "FROM python:3.11-slim@sha256:",
        "FROM node:20-alpine@sha256:",
        "FROM nginx:alpine@sha256:",
    ]:
        haystack = dockerfile if token.startswith("FROM python") else nginx_dockerfile
        if token not in haystack:
            findings.append(f"reviewed Dockerfiles are missing pinned base image token {token}")

    for expected in [
        "pip install pip-audit cyclonedx-bom -r requirements.release.txt",
        "pip-audit -r requirements.release.txt",
        "cyclonedx_py requirements requirements.release.txt -o python-sbom.json",
    ]:
        if expected not in security_workflow:
            findings.append(f"security workflow is missing reviewed release-manifest command: {expected}")

    if findings:
        print("Release provenance audit failed:")
        for finding in findings:
            print(f" - {finding}")
        return 1

    print("Release provenance audit passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
