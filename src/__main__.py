"""Synedrios — AI Meeting Participant.

Entry point. Initialises the database, loads mode configurations,
creates the LLM adapter, and starts the Slack connector.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from src.connectors.slack import SlackConnector
from src.engine.modes import load_all_modes
from src.envfile import load_env_file
from src.llm.adapter import create_adapter
from src.persistence.database import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("synedrios")


def main() -> None:
    """Initialise and run Synedrios."""
    logger.info("Starting Synedrios...")

    # Bare-metal runs read .env themselves; under Docker, env_file already
    # populated the environment and this is a no-op (real env always wins).
    load_env_file()

    # Validate required environment variables
    required = ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "ANTHROPIC_API_KEY"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        logger.error(
            "Missing required environment variables: %s\n"
            "Copy .env.example to .env and fill in the values.",
            ", ".join(missing),
        )
        sys.exit(1)

    # Initialise database
    db = Database()
    logger.info("Database initialised at %s", db.db_path)

    # Load meeting modes
    modes = load_all_modes("config/modes")
    if not modes:
        logger.error("No meeting modes found in config/modes/")
        sys.exit(1)
    logger.info("Loaded %d meeting modes: %s", len(modes), ", ".join(modes.keys()))

    # Create LLM adapter
    llm_config = {
        "provider": os.environ.get("LLM_PROVIDER", "anthropic"),
        "api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
        "model": os.environ.get("LLM_MODEL", "claude-sonnet-5"),
        "base_url": os.environ.get("LLM_BASE_URL", ""),
        # Only needed for identity-linked Console keys; harmless otherwise.
        "workspace_id": os.environ.get("ANTHROPIC_WORKSPACE_ID", ""),
    }
    llm = create_adapter(llm_config)
    logger.info("LLM adapter: %s", llm.name)

    # Participant name
    participant_name = os.environ.get("PARTICIPANT_NAME", "Synedrios")

    # Create and start Slack connector
    connector = SlackConnector(
        db=db,
        llm=llm,
        modes=modes,
        participant_name=participant_name,
    )

    logger.info(
        "%s is ready. Invite the bot to a Slack channel and mention it to start a meeting.",
        participant_name,
    )

    asyncio.run(connector.start())


if __name__ == "__main__":
    main()
