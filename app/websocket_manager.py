import asyncio
import logging
from typing import Dict, List

from fastapi import WebSocket
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, task_id: str):
        await websocket.accept()

        old_connections = self.active_connections.get(task_id, [])
        for old_ws in old_connections:
            if old_ws is websocket:
                continue
            try:
                if old_ws.client_state == WebSocketState.CONNECTED:
                    await old_ws.close(code=1000, reason="Replaced by a new connection")
            except Exception:
                pass

        self.active_connections[task_id] = [websocket]
        logger.info("[WS] Client connected to task %s", task_id)

    def disconnect(self, websocket: WebSocket, task_id: str):
        current = self.active_connections.get(task_id)
        if not current:
            return
        try:
            current.remove(websocket)
        except ValueError:
            pass
        if not current:
            self.active_connections.pop(task_id, None)
        logger.info("[WS] Client disconnected from task %s", task_id)

    @staticmethod
    def is_open(websocket: WebSocket) -> bool:
        return websocket.client_state == WebSocketState.CONNECTED

    async def send_personal_message(
        self,
        message: dict,
        websocket: WebSocket,
        task_id: str = "",
        timeout_seconds: float = 3.0,
    ) -> bool:
        try:
            if not self.is_open(websocket):
                return False
            await asyncio.wait_for(websocket.send_json(message), timeout=timeout_seconds)
            self._log_message_summary(message, task_id)
            return True
        except Exception as exc:
            logger.warning("[WS] Send failed for task %s: %s", task_id, exc)
            return False

    async def broadcast(self, message: dict, task_id: str):
        sockets = self.active_connections.get(task_id, [])
        if not sockets:
            return

        for websocket in list(sockets):
            ok = await self.send_personal_message(message, websocket, task_id=task_id)
            if not ok:
                self.disconnect(websocket, task_id)

    @staticmethod
    def _log_message_summary(message: dict, task_id: str = ""):
        msg_type = message.get("type", "UNKNOWN")
        if msg_type in {"PING", "PONG"}:
            return
        if msg_type == "LOG":
            agent = message.get("agent", "Unknown")
            level = str(message.get("level", "INFO")).upper()
            content = str(message.get("message", ""))
            snippet = (content[:80] + "...") if len(content) > 80 else content
            logger.info("[WS] [%s] [%s] %s", task_id, agent, snippet.replace("\n", " "))
            if level == "ERROR":
                logger.error("[WS] [%s] Agent error log emitted", task_id)
            return
        logger.info("[WS] [%s] %s", task_id, msg_type)


manager = ConnectionManager()