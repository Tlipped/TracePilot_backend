from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import uuid
import json
from datetime import datetime

from .models import TaskCreateRequest, TaskResponse, TaskStatus, LogMessage
from .task_manager import TaskManager, get_task_manager
from .websocket_manager import manager
from app.database.redis_client import redis_client
from app.database.models import TaskLog
from app.database import SessionLocal
import logging

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)

app = FastAPI(title="TracePilot Backend API")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.websocket("/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str, task_manager: TaskManager = Depends(get_task_manager)):
    await manager.connect(websocket, task_id)
    
    queue = task_manager.get_task_queue(task_id)
    if not queue:
        await websocket.close(code=4004, reason="Task not found")
        return

    try:
        # 发送连接成功消息
        await manager.send_personal_message({
            "type": "CONNECTED",
            "task_id": task_id,
            "message": "WebSocket connected successfully",
            "timestamp": datetime.now().isoformat()
        }, websocket, task_id)

        while True:
            try:
                # 等待消息，超时 10 秒
                log_msg = await asyncio.wait_for(queue.get(), timeout=10.0)
                
                # 确保消息格式正确
                if isinstance(log_msg, dict):
                    if log_msg.get("type") == "LOG":
                        msg_to_send = {
                            "agent": log_msg.get("agent", "Unknown"),
                            "level": log_msg.get("level", "INFO"),
                            "message": log_msg.get("message", ""),
                            "message_type": log_msg.get("message_type", "text"),
                            "is_truncated": log_msg.get("is_truncated", False),
                            "timestamp": log_msg.get("timestamp", datetime.now().isoformat()),
                            "log_id": log_msg.get("log_id")
                        }
                        await manager.send_personal_message(msg_to_send, websocket, task_id)
                    
            except asyncio.TimeoutError:
                # 心跳
                try:
                    await websocket.send_json({
                        "type": "PING",
                        "timestamp": datetime.now().isoformat()
                    })
                except:
                    break
            
            except asyncio.CancelledError:
                print(f"[WS] ⏹️  Task {task_id} was cancelled")
                break
            
            except Exception as e:
                if "close message" not in str(e):
                    print(f"[WS] Error: {type(e).__name__}")
                break
    
    except Exception as e:
        if "WebSocketDisconnect" not in str(type(e)):
            print(f"[WS] Unexpected error in websocket_endpoint: {e}")
    
    finally:
        manager.disconnect(websocket, task_id)
        try:
            await websocket.close()
        except:
            pass

# Task endpoints
@app.post("/api/tasks", response_model=TaskResponse)
async def create_task(
    request: TaskCreateRequest, 
    task_manager: TaskManager = Depends(get_task_manager)
):
    """创建任务并立即在后台启动工作流"""
    task_id = task_manager.create_task(request.dapp_name)
    task_manager.start_task(task_id, request.dapp_name)
    
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=500, detail="Failed to create task")
    return task

@app.get("/api/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str, 
    task_manager: TaskManager = Depends(get_task_manager)
):
    """获取单个任务状态"""
    task = task_manager.get_task(task_id)  # 同步方法
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

@app.get("/api/tasks")
async def list_tasks(task_manager: TaskManager = Depends(get_task_manager)):
    """获取所有任务列表"""
    return await task_manager.list_tasks()

@app.get("/api/task/{task_id}/log/{log_id}")
async def get_full_log(task_id: str, log_id: str):
    """获取完整日志内容"""
    content = redis_client.get_log(log_id)
    if content:
        return {"content": content, "source": "cache"}

    db_session = SessionLocal()
    try:
        log = db_session.query(TaskLog).filter(
            TaskLog.task_id == task_id,
            TaskLog.log_id == log_id
        ).first()
        if log:
            return {"content": log.full_content, "source": "database"}
        raise HTTPException(status_code=404, detail="Log not found")
    finally:
        db_session.close()

@app.delete("/api/tasks/{task_id}")
async def cancel_task(
    task_id: str, 
    task_manager: TaskManager = Depends(get_task_manager)
):
    """取消任务"""
    success = task_manager.cancel_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found or already completed")
    return {"message": "Task cancelled"}

@app.on_event("startup")
async def startup():
    print("[App] FastAPI server starting...")

@app.on_event("shutdown")
async def shutdown():
    print("[App] FastAPI server shutting down...")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)