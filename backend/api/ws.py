"""WebSocket 实时日志流：/ws/logs?token=xxx"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import auth
from ..core import logstream

router = APIRouter()


@router.websocket("/ws/logs")
async def ws_logs(ws: WebSocket, token: str = ""):
    if not auth.check_token(token):
        await ws.close(code=4001)
        return
    await ws.accept()
    queue = logstream.subscribe()
    try:
        # 先补发最近的日志
        for entry in logstream.recent(100):
            await ws.send_json(entry)
        while True:
            entry = await queue.get()
            await ws.send_json(entry)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        logstream.unsubscribe(queue)
