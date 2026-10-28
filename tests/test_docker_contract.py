"""Deployment contracts, graded statically (the sandbox has no
Docker daemon — the same honesty the README states): the Dockerfile
is multi-stage, non-root, ARM64-noted and serves 8080; compose
carries the production store and object store."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_contract():
    text = (ROOT / "Dockerfile").read_text()
    assert text.count("FROM ") >= 2, "multi-stage build"
    assert "arm64" in text.lower()
    assert "USER app" in text
    assert "8080" in text
    assert "PYTHONUNBUFFERED=1" in text


def test_compose_contract():
    text = (ROOT / "docker-compose.yml").read_text()
    assert "pgvector/pgvector:pg16" in text
    assert "minio:" in text
    assert "seaweedfs" in text.lower()
    assert "DATABASE_URL" in text
