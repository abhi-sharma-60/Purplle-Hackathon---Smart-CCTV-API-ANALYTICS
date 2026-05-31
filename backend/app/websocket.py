import json
import datetime
import logging
from typing import Any, List
from fastapi import WebSocket

logger = logging.getLogger("store_intelligence.websocket")

def _serialize_default(obj: Any) -> Any:
    """JSON fallback serializer for datetime and other non-standard types."""
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    if isinstance(obj, datetime.date):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

class WebSocketManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"New WebSocket client connected. Active connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket client disconnected. Active connections: {len(self.active_connections)}")

    async def send_personal_message(self, message: dict, websocket: WebSocket):
        try:
            text = json.dumps(message, default=_serialize_default)
            await websocket.send_text(text)
        except Exception as e:
            logger.warning(f"Failed to send direct WebSocket message: {str(e)}")
            self.disconnect(websocket)

    async def broadcast_json(self, message: dict):
        if not self.active_connections:
            return
        
        disconnected_clients = []
        text = json.dumps(message, default=_serialize_default)
        for connection in self.active_connections:
            try:
                await connection.send_text(text)
            except Exception as e:
                logger.warning(f"Failed to broadcast WebSocket message: {str(e)}")
                disconnected_clients.append(connection)
        
        for connection in disconnected_clients:
            self.disconnect(connection)

# Singleton manager
ws_manager = WebSocketManager()
