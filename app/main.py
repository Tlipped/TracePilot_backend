import asyncio
import base64
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.database import SessionLocal
from app.database.models import TaskLog
from app.database.redis_client import redis_client
from settings import PROJECT_PATH
from .models import TaskCreateRequest, TaskResponse, TaskStatus
from .task_manager import TaskManager, get_task_manager
from .websocket_manager import manager

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

app = FastAPI(title="TracePilot Backend API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


AGENT_LOG_ROOT = Path(PROJECT_PATH) / "agents" / "logs"
MAX_AGENT_LOG_BYTES = 2_000_000


def _b64_encode(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _b64_decode(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii")).decode("utf-8")


def _safe_relative_path(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _parse_log_session_time(session_name: str):
    try:
        return datetime.strptime(session_name, "%Y-%m-%d_%H-%M")
    except ValueError:
        return None


def find_agent_log_dir(task: TaskResponse) -> Path | None:
    if not AGENT_LOG_ROOT.exists():
        return None

    candidates: List[Path] = []
    for session_dir in AGENT_LOG_ROOT.iterdir():
        if not session_dir.is_dir():
            continue
        dapp_dir = session_dir / task.dapp_name
        if dapp_dir.is_dir():
            candidates.append(dapp_dir)

    if not candidates:
        return None

    def score(path: Path):
        session_time = _parse_log_session_time(path.parent.name)
        if session_time:
            return abs((session_time - task.created_at.replace(tzinfo=None)).total_seconds())
        try:
            return abs(path.stat().st_mtime - task.created_at.timestamp())
        except Exception:
            return float("inf")

    return min(candidates, key=score)


def load_persisted_log_events(task_id: str, dapp_name: str = "", limit: int = 1000):
    db_session = SessionLocal()
    try:
        logs = (
            db_session.query(TaskLog)
            .filter(TaskLog.task_id.in_([task_id, dapp_name] if dapp_name else [task_id]))
            .order_by(TaskLog.timestamp.desc(), TaskLog.id.desc())
            .limit(limit)
            .all()
        )
        events = []
        for log in reversed(logs):
            events.append(
                {
                    "type": "LOG",
                    "task_id": task_id,
                    "agent": log.agent or "Unknown",
                    "level": log.level or "info",
                    "message": log.message or "",
                    "message_type": log.message_type or "text",
                    "is_truncated": bool(log.is_truncated),
                    "timestamp": log.timestamp.isoformat() if log.timestamp else datetime.now().isoformat(),
                    "log_id": log.log_id,
                }
            )
        return events
    finally:
        db_session.close()


@app.websocket("/ws/{task_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    task_id: str,
    task_manager: TaskManager = Depends(get_task_manager),
):
    await manager.connect(websocket, task_id)

    task_queue = task_manager.get_task_queue(task_id)
    task_snapshot = task_manager.get_task(task_id)
    if not task_queue or not task_snapshot:
        manager.disconnect(websocket, task_id)
        await websocket.close(code=4004, reason="Task not found")
        return

    outbound_queue: asyncio.Queue = asyncio.Queue(maxsize=1200)
    stop_event = asyncio.Event()
    terminal_status = {TaskStatus.COMPLETED.value, TaskStatus.FAILED.value}
    heartbeat_interval = 10.0
    heartbeat_timeout = 35.0
    last_pong_at = time.monotonic()
    pong_supported = False
    dropped_outbound_logs = 0

    def drop_one_outbound_log() -> bool:
        if outbound_queue.empty():
            return False

        size = outbound_queue.qsize()
        kept_items = []
        dropped = False
        for _ in range(size):
            item = outbound_queue.get_nowait()
            if not dropped and isinstance(item, dict) and item.get("type") == "LOG":
                dropped = True
                continue
            kept_items.append(item)

        for item in kept_items:
            outbound_queue.put_nowait(item)
        return dropped

    def enqueue_outbound(payload: Dict[str, Any]):
        nonlocal dropped_outbound_logs
        msg_type = payload.get("type")
        if msg_type == "LOG" and outbound_queue.full():
            dropped_outbound_logs += 1
            return

        if outbound_queue.full():
            dropped = drop_one_outbound_log()
            if not dropped and outbound_queue.full():
                try:
                    outbound_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass

        if not outbound_queue.full():
            outbound_queue.put_nowait(payload)

    def flush_drop_notice():
        nonlocal dropped_outbound_logs
        if dropped_outbound_logs <= 0:
            return
        enqueue_outbound(
            {
                "type": "LOG_DROPPED",
                "task_id": task_id,
                "count": dropped_outbound_logs,
                "message": "WebSocket outbound queue was congested; partial logs were dropped.",
                "timestamp": datetime.now().isoformat(),
            }
        )
        dropped_outbound_logs = 0

    def normalize_queue_message(message: Dict[str, Any]) -> Dict[str, Any]:
        msg_type = message.get("type", "LOG")
        if msg_type != "LOG":
            return message
        return {
            "type": "LOG",
            "task_id": task_id,
            "agent": message.get("agent", "Unknown"),
            "level": message.get("level", "info"),
            "message": message.get("message", ""),
            "message_type": message.get("message_type", "text"),
            "is_truncated": message.get("is_truncated", False),
            "timestamp": message.get("timestamp", datetime.now().isoformat()),
            "log_id": message.get("log_id"),
        }

    async def sender():
        while True:
            if stop_event.is_set() and outbound_queue.empty():
                return
            try:
                payload = await asyncio.wait_for(outbound_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            ok = await manager.send_personal_message(payload, websocket, task_id=task_id)
            if not ok:
                stop_event.set()
                return

    async def pump_task_events():
        while not stop_event.is_set():
            try:
                queue_msg = await asyncio.wait_for(task_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                current = task_manager.get_task(task_id)
                if (
                    current
                    and current.status.value in terminal_status
                    and task_queue.empty()
                    and outbound_queue.empty()
                ):
                    stop_event.set()
                    return
                continue

            if not isinstance(queue_msg, dict):
                continue

            normalized = normalize_queue_message(queue_msg)
            if normalized.get("type") != "LOG":
                flush_drop_notice()
            enqueue_outbound(normalized)

            if normalized.get("type") == "TASK_FINAL":
                stop_event.set()
                return

    async def receiver():
        nonlocal last_pong_at, pong_supported
        while not stop_event.is_set():
            try:
                incoming = await asyncio.wait_for(websocket.receive_json(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                stop_event.set()
                return
            except Exception:
                stop_event.set()
                return

            if not isinstance(incoming, dict):
                continue

            msg_type = str(incoming.get("type", "")).upper()
            if msg_type == "PONG":
                pong_supported = True
                last_pong_at = time.monotonic()
            elif msg_type == "PING":
                enqueue_outbound(
                    {
                        "type": "PONG",
                        "task_id": task_id,
                        "timestamp": datetime.now().isoformat(),
                    }
                )

    async def heartbeat():
        while not stop_event.is_set():
            await asyncio.sleep(heartbeat_interval)
            if stop_event.is_set():
                return

            if pong_supported and (time.monotonic() - last_pong_at > heartbeat_timeout):
                enqueue_outbound(
                    {
                        "type": "HEARTBEAT_TIMEOUT",
                        "task_id": task_id,
                        "message": "No PONG received within heartbeat timeout window.",
                        "timestamp": datetime.now().isoformat(),
                    }
                )
                stop_event.set()
                return

            enqueue_outbound(
                {
                    "type": "PING",
                    "task_id": task_id,
                    "timestamp": datetime.now().isoformat(),
                }
            )

    enqueue_outbound(
        {
            "type": "CONNECTED",
            "task_id": task_id,
            "message": "WebSocket connected successfully",
            "timestamp": datetime.now().isoformat(),
        }
    )
    if task_snapshot.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
        for event in load_persisted_log_events(task_id, task_snapshot.dapp_name):
            enqueue_outbound(event)
    enqueue_outbound(
        {
            "type": "TASK_STATUS",
            "task_id": task_snapshot.task_id,
            "dapp_name": task_snapshot.dapp_name,
            "status": task_snapshot.status.value,
            "created_at": task_snapshot.created_at.isoformat() if task_snapshot.created_at else None,
            "completed_at": task_snapshot.completed_at.isoformat() if task_snapshot.completed_at else None,
            "duration": task_snapshot.duration,
            "error": task_snapshot.error,
        }
    )

    sender_task = asyncio.create_task(sender())
    pump_task = asyncio.create_task(pump_task_events())
    receiver_task = asyncio.create_task(receiver())
    heartbeat_task = asyncio.create_task(heartbeat())

    try:
        await asyncio.wait(
            {sender_task, pump_task, receiver_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        stop_event.set()
        await asyncio.gather(pump_task, receiver_task, heartbeat_task, return_exceptions=True)
        await asyncio.gather(sender_task, return_exceptions=True)
        manager.disconnect(websocket, task_id)
        try:
            await websocket.close()
        except Exception:
            pass


@app.post("/api/tasks", response_model=TaskResponse)
async def create_task(
    request: TaskCreateRequest,
    task_manager: TaskManager = Depends(get_task_manager),
):
    task_id = task_manager.create_task(request.dapp_name)
    task_manager.start_task(task_id, request.dapp_name)

    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=500, detail="Failed to create task")
    return task


@app.get("/api/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    task_manager: TaskManager = Depends(get_task_manager),
):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.get("/api/tasks")
async def list_tasks(task_manager: TaskManager = Depends(get_task_manager)):
    return await task_manager.list_tasks()


@app.get("/api/task/{task_id}/log/{log_id}")
async def get_full_log(
    task_id: str,
    log_id: str,
    task_manager: TaskManager = Depends(get_task_manager),
):
    content = redis_client.get_log(log_id)
    if content:
        return {"content": content, "source": "cache"}

    task = task_manager.get_task(task_id)
    task_ids = [task_id]
    if task:
        task_ids.append(task.dapp_name)

    db_session = SessionLocal()
    try:
        log = db_session.query(TaskLog).filter(
            TaskLog.task_id.in_(task_ids),
            TaskLog.log_id == log_id,
        ).first()
        if log:
            return {"content": log.full_content or log.message or "", "source": "database"}
        raise HTTPException(status_code=404, detail="Log not found")
    finally:
        db_session.close()


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    task_manager: TaskManager = Depends(get_task_manager),
):
    success = task_manager.cancel_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found or already completed")
    return {"message": "Task cancelled"}


@app.delete("/api/tasks/{task_id}")
async def delete_task(
    task_id: str,
    task_manager: TaskManager = Depends(get_task_manager),
):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
        success = task_manager.cancel_task(task_id)
        if not success:
            raise HTTPException(status_code=409, detail="Task is not deletable yet")
        return {"message": "Task cancelled", "action": "cancelled"}

    success = task_manager.delete_task(task_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete task")
    return {"message": "Task deleted", "action": "deleted"}


@app.get("/api/tasks/{task_id}/agent-log-files")
async def list_agent_log_files(
    task_id: str,
    task_manager: TaskManager = Depends(get_task_manager),
):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    log_dir = find_agent_log_dir(task)
    if not log_dir:
        return {"task_id": task_id, "dapp_name": task.dapp_name, "log_dir": None, "files": []}

    files = []
    for file_path in sorted(log_dir.glob("*.log"), key=lambda path: path.name.lower()):
        try:
            stat = file_path.stat()
            relative_path = _safe_relative_path(file_path, AGENT_LOG_ROOT)
        except Exception:
            continue
        files.append(
            {
                "id": _b64_encode(relative_path),
                "name": file_path.name,
                "agent": file_path.stem,
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            }
        )

    return {
        "task_id": task_id,
        "dapp_name": task.dapp_name,
        "log_dir": _safe_relative_path(log_dir, AGENT_LOG_ROOT),
        "files": files,
    }


@app.get("/api/tasks/{task_id}/agent-log-files/{file_id}")
async def get_agent_log_file(
    task_id: str,
    file_id: str,
    max_bytes: int = MAX_AGENT_LOG_BYTES,
    task_manager: TaskManager = Depends(get_task_manager),
):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    try:
        relative_path = _b64_decode(file_id)
        file_path = (AGENT_LOG_ROOT / relative_path).resolve()
        file_path.relative_to(AGENT_LOG_ROOT.resolve())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid log file id")

    if not file_path.is_file() or file_path.suffix.lower() != ".log":
        raise HTTPException(status_code=404, detail="Log file not found")
    if file_path.parent.name != task.dapp_name:
        raise HTTPException(status_code=403, detail="Log file does not belong to this task")

    max_bytes = min(max(max_bytes, 1), MAX_AGENT_LOG_BYTES)
    raw = file_path.read_bytes()
    truncated = len(raw) > max_bytes
    content = raw[:max_bytes].decode("utf-8", errors="replace")
    stat = file_path.stat()

    return {
        "task_id": task_id,
        "dapp_name": task.dapp_name,
        "id": file_id,
        "name": file_path.name,
        "agent": file_path.stem,
        "content": content,
        "size": stat.st_size,
        "truncated": truncated,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }


@app.on_event("startup")
async def startup():
    logger.info("[App] FastAPI server starting...")


@app.on_event("shutdown")
async def shutdown():
    logger.info("[App] FastAPI server shutting down...")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
