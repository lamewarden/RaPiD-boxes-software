"""Entry point: `python -m rapidboxes` or the `rapidboxes` console script."""
from __future__ import annotations

import uvicorn

from .config import get_config


def main() -> None:
    config = get_config()
    # Cap graceful shutdown: open WebSockets otherwise block exit indefinitely
    # when the kiosk requests /api/system/restart-service.
    uvicorn.run(
        "rapidboxes.main:app",
        host=config.host,
        port=config.port,
        log_level="info",
        timeout_graceful_shutdown=2,
    )


if __name__ == "__main__":
    main()
