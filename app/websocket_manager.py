from typing import List, Dict, Set
from fastapi import WebSocket
import logging
import asyncio  

logger = logging.getLogger(__name__)

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}
        self.task_agents: Dict[str, str] = {}
        self.task_locks: Dict[str, asyncio.Lock] = {}  # ✅ 防止并发

    async def connect(self, websocket: WebSocket, task_id: str):
        """✅ 改进：一个 task_id 只允许一个活跃连接"""
        await websocket.accept()
        
        # 获取或创建任务的锁
        if task_id not in self.task_locks:
            self.task_locks[task_id] = asyncio.Lock()
        
        # 关闭旧连接（如果存在）
        if task_id in self.active_connections:
            for old_ws in self.active_connections[task_id]:
                try:
                    await old_ws.close(code=1000, reason="New connection established")
                except:
                    pass
            self.active_connections[task_id] = []
        
        if task_id not in self.active_connections:
            self.active_connections[task_id] = []
        
        self.active_connections[task_id].append(websocket)
        print(f"[WS] ✅ Client connected to task {task_id} (total: {len(self.active_connections[task_id])})")

    def disconnect(self, websocket: WebSocket, task_id: str):
        """✅ 改进：安全断开连接"""
        if task_id in self.active_connections:
            try:
                self.active_connections[task_id].remove(websocket)
            except ValueError:
                pass
            
            if not self.active_connections[task_id]:
                del self.active_connections[task_id]
                if task_id in self.task_agents:
                    del self.task_agents[task_id]
        
        print(f"[WS] ❌ Client disconnected from task {task_id}")

    async def send_personal_message(self, message: dict, websocket: WebSocket, task_id: str = None):
        """✅ 改进：检查连接状态再发送"""
        try:
            # 检查 WebSocket 是否仍然活跃
            if websocket.client_state.value != 1:  # 1 = open
                return
            
            # 打印摘要
            self._log_message_summary(message, task_id)
            
            # 发送消息
            await websocket.send_json(message)
        except RuntimeError as e:
            # 连接已关闭
            if "close message has been sent" in str(e):
                # 忽略，连接已断开
                pass
            else:
                print(f"[WS] ⚠️  Send failed: {e}")
        except Exception as e:
            print(f"[WS] ⚠️  Unexpected error: {type(e).__name__}: {e}")

    async def broadcast(self, message: dict, task_id: str):
        """广播消息，跳过断开的连接"""
        if task_id not in self.active_connections:
            return
        
        # 打印摘要
        self._log_message_summary(message, task_id)
        
        # 过滤活跃的连接
        active = [ws for ws in self.active_connections[task_id] if ws.client_state.value == 1]
        
        for websocket in active:
            try:
                await websocket.send_json(message)
            except Exception as e:
                # 忽略已断开的连接
                pass

    def _log_message_summary(self, message: dict, task_id: str = None):
        """打印发送给前端的消息摘要"""
        msg_type = message.get("type", "UNKNOWN")
        
        if msg_type == "CONNECTED":
            print(f"[WS] 🟢 CONNECTED - Task: {task_id}")
        elif msg_type == "PING":
            pass  # 心跳不打印
        elif msg_type == "LOG":
            agent = message.get("agent", "Unknown")
            level = message.get("level", "INFO")
            msg_subtype = message.get("message_type", "TEXT")
            content = message.get("message", "")
            
            if task_id:
                self.task_agents[task_id] = agent
            
            content_snippet = content[:80].replace('\n', ' ') if content else ""
            if len(content) > 80:
                content_snippet += "..."
            
            level_emoji = {
                "INFO": "ℹ️",
                "ERROR": "❌",
                "WARNING": "⚠️",
                "DEBUG": "🐛"
            }.get(level, "📝")
            
            subtype_emoji = {
                "MARKDOWN": "📄",
                "TEXT": "💬",
                "TOOL_CALL": "🛠️",
                "RESULT": "✅",
                "JSON": "📊"
            }.get(msg_subtype, "📝")
            
            print(f"[WS] {level_emoji} {subtype_emoji} [{agent}] {content_snippet}")

manager = ConnectionManager()