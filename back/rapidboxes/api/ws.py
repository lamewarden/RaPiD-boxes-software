"""WebSocket: pushes live ExperimentStatus to the UI."""
from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger("rapidboxes.ws")
router = APIRouter()


@router.websocket("/api/ws")
async def status_ws(websocket: WebSocket):
    await websocket.accept()
    runner = websocket.app.state.app.runner

    async def send(payload: dict) -> None:
        await websocket.send_json(payload)

    runner.subscribe(send)
    try:
        # Push the current snapshot immediately so a fresh client is in sync.
        await websocket.send_json(runner.status.model_dump(mode="json"))
        while True:
            await websocket.receive_text()  # blocks until the client closes
    except WebSocketDisconnect:
        pass
    except Exception:
        log.debug("ws error", exc_info=True)
    finally:
        runner.unsubscribe(send)
