"""WebSocket 实时日志流：/ws/logs

鉴权两条通道：
1. Sec-WebSocket-Protocol: `bearer,<token>` 子协议（老前端兼容；避免 JWT 出现在
   URL 查询串里——uvicorn 访问日志/中间代理会原样记录完整 URL）；
2. 登录态 HttpOnly Cookie（sa_auth）：浏览器 WebSocket 握手天然携带同源 Cookie，
   前端无需再保存 token。
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import auth
from ..core import logstream

router = APIRouter()


def _token_from_protocol(ws: WebSocket) -> str:
    """从 Sec-WebSocket-Protocol: bearer,<token> 提取 token；格式不符返回空。"""
    protos = [p.strip() for p in
              ws.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
    if len(protos) >= 2 and protos[0].lower() == "bearer":
        return protos[1]
    return ""


@router.websocket("/ws/logs")
async def ws_logs(ws: WebSocket):
    token = _token_from_protocol(ws)
    if not auth.check_token(token):
        # 子协议未携带有效 token 时回落到登录态 Cookie
        token = str(ws.cookies.get("sa_auth") or "")
        if not auth.check_token(token):
            await ws.close(code=4001)
            return
    # 浏览器要求服务端从其请求的子协议列表中回应一个，否则握手失败：
    # 仅当客户端确实请求了子协议时才回 'bearer'，纯 Cookie 连接直接 accept
    requested_protos = [p.strip() for p in
                        ws.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
    if requested_protos:
        await ws.accept(subprotocol="bearer")
    else:
        await ws.accept()
    queue = logstream.subscribe()
    try:
        # 先补发最近的日志（条目带递增 seq，前端对已显示过的 seq 去重，重连不会重复刷屏）
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
