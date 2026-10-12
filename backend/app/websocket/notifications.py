from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        self.active: dict[int, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_id: int) -> None:
        await websocket.accept()
        self.active.setdefault(user_id, []).append(websocket)

    def disconnect(self, websocket: WebSocket, user_id: int) -> None:
        conns = self.active.get(user_id, [])
        if websocket in conns:
            conns.remove(websocket)

    async def send_to_user(self, user_id: int, message: dict[str, Any]) -> None:
        for ws in self.active.get(user_id, []):
            await ws.send_json(message)


manager = ConnectionManager()


@router.websocket("/ws/notifications/{user_id}")
async def websocket_notifications(websocket: WebSocket, user_id: int) -> None:
    await manager.connect(websocket, user_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id)
