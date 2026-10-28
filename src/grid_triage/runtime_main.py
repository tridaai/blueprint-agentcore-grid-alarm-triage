"""Serve the AgentCore Runtime app: ``grid-agent-serve``.

This is the container entrypoint in the Dockerfile. Locally it binds
the same 8080 contract AgentCore Runtime expects in production."""

from __future__ import annotations

from grid_triage.config import Settings
from grid_triage.logging_config import configure_logging
from grid_triage.runtime_app import app


def main() -> None:
    configure_logging()
    settings = Settings.from_env()
    app.run(port=settings.runtime_port, host="0.0.0.0")


if __name__ == "__main__":
    main()
