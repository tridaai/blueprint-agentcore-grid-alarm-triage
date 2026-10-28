"""Configuration from the environment.

The .env.example leads with the AWS profile because that is the
production shape: a Bedrock model in a named region, the agent on
AgentCore Runtime. The default profile is offline so the repo runs
anywhere — including CI — with no account and no keys.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    # AWS / Bedrock profile (production).
    aws_region: str = "us-east-1"
    bedrock_model_id: str = ""
    model_profile: str = "offline"  # offline | bedrock

    # Stores. SQLite is the local default; DATABASE_URL switches the
    # store port to PostgreSQL.
    sqlite_path: str = "./data/grid.db"
    database_url: str = ""
    object_store_dir: str = "./data/objects"

    topology_version: str = "v1"
    runtime_port: int = 8080

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
            bedrock_model_id=os.environ.get("BEDROCK_MODEL_ID", ""),
            model_profile=os.environ.get("GRID_MODEL_PROFILE", "offline"),
            sqlite_path=os.environ.get("SQLITE_PATH", "./data/grid.db"),
            database_url=os.environ.get("DATABASE_URL", ""),
            object_store_dir=os.environ.get("OBJECT_STORE_DIR", "./data/objects"),
            topology_version=os.environ.get("TOPOLOGY_VERSION", "v1"),
            runtime_port=int(os.environ.get("RUNTIME_PORT", "8080")),
        )
