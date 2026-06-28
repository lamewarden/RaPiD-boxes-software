"""Entry point: `python -m rapidboxes` or the `rapidboxes` console script."""
from __future__ import annotations

import uvicorn

from .config import get_config


def main() -> None:
    config = get_config()
    uvicorn.run("rapidboxes.main:app", host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
