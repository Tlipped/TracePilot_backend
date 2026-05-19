import asyncio
import base64
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.database import SessionLocal, init_db
from app.database.models import TaskLog
from app.database.redis_client import redis_client
from settings import PROJECT_PATH
from .models import DappCatalogItem, DappCatalogResponse, TaskCreateRequest, TaskResponse, TaskStatus
from .review_service import build_automated_review
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
PROCESSED_DATA_ROOT = Path(PROJECT_PATH) / "dataset" / "processed"
RAW_DATA_ROOT = Path(PROJECT_PATH) / "dataset" / "raw"


def _b64_encode(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _b64_decode(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii")).decode("utf-8")


def _safe_relative_path(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _safe_processed_file_name(dapp_name: str) -> str:
    return dapp_name.replace("/", "_").replace("\\", "_")


def _load_json_file(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _as_reference(value: Any) -> Dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "time": value.get("time"),
        "link": value.get("link"),
    }


def _short_hash(value: str) -> str:
    return f"{value[:10]}...{value[-8:]}" if len(value) > 18 else value


def _summarize_balance_change(tx_hash: str, balance_change: Dict[str, Any]) -> Dict[str, Any]:
    tx_balance = balance_change.get(tx_hash, {}) if isinstance(balance_change, dict) else {}
    participants = []
    total_usd_delta = 0.0

    for address, token_changes in tx_balance.items():
        token_summary = []
        address_usd_delta = 0.0
        if not isinstance(token_changes, dict):
            continue
        for token_address, change in token_changes.items():
            if not isinstance(change, dict):
                continue
            usd_value = change.get("usd_value")
            if isinstance(usd_value, (int, float)):
                address_usd_delta += usd_value
                total_usd_delta += usd_value
            token_summary.append(
                {
                    "token": change.get("symbol") or token_address,
                    "name": change.get("name"),
                    "amount": change.get("fmt_amount"),
                    "usd_value": usd_value,
                    "contract": change.get("contract"),
                }
            )
        participants.append(
            {
                "address": address,
                "usd_delta": address_usd_delta,
                "tokens": token_summary[:8],
            }
        )

    participants.sort(key=lambda item: abs(item.get("usd_delta") or 0), reverse=True)
    return {
        "participant_count": len(participants),
        "total_usd_delta": total_usd_delta,
        "top_participants": participants[:8],
    }


def _summarize_transaction(
    tx_hash: str,
    processed_data: Dict[str, Any],
    index: int,
) -> Dict[str, Any]:
    detail = (processed_data.get("transaction_to_detail") or {}).get(tx_hash, {}) or {}
    attack_transactions = set(processed_data.get("attack_transactions") or [])
    auxiliary_transactions = set(processed_data.get("auxiliary_transactions") or [])
    debug_targets = set(processed_data.get("transactions_need_analyze") or [])
    address_roles = processed_data.get("transaction_roles") or {}
    from_addr = str(detail.get("from") or "").lower()
    to_addr = str(detail.get("to") or detail.get("contract_address") or "").lower()

    if tx_hash in attack_transactions:
        tx_type = "attack"
    elif tx_hash in auxiliary_transactions:
        tx_type = "auxiliary"
    else:
        tx_type = "candidate"

    involved_roles = []
    for address in [from_addr, to_addr]:
        role_item = address_roles.get(address) if isinstance(address_roles, dict) else None
        if role_item:
            involved_roles.append(
                {
                    "address": address,
                    "role": role_item.get("role"),
                    "description": role_item.get("description"),
                }
            )

    logs = ((detail.get("log_analysis") or {}).get("detailed_logs") or [])[:8]
    return {
        "hash": tx_hash,
        "display_hash": _short_hash(tx_hash),
        "type": tx_type,
        "is_debug_target": tx_hash in debug_targets,
        "order": index,
        "from": from_addr or None,
        "to": to_addr or None,
        "from_role": (address_roles.get(from_addr) or {}).get("role") if isinstance(address_roles, dict) else None,
        "to_role": (address_roles.get(to_addr) or {}).get("role") if isinstance(address_roles, dict) else None,
        "involved_roles": involved_roles,
        "block_number": detail.get("block_number"),
        "timestamp": detail.get("timestamp"),
        "function_signature": detail.get("function_signature"),
        "status": (detail.get("status") or {}).get("description"),
        "success": (detail.get("status") or {}).get("success"),
        "value_eth": (detail.get("value") or {}).get("eth"),
        "gas_used": (detail.get("gas") or {}).get("used"),
        "cost_eth": (detail.get("cost") or {}).get("total_eth"),
        "interacted_addresses": ((detail.get("log_analysis") or {}).get("interacted_addresses") or [])[:12],
        "event_logs": logs,
        "balance_summary": _summarize_balance_change(tx_hash, processed_data.get("balance_change") or {}),
    }


def _load_macro_analysis_payload(task: TaskResponse) -> Dict[str, Any]:
    processed_file = PROCESSED_DATA_ROOT / f"{_safe_processed_file_name(task.dapp_name)}.json"
    if not processed_file.exists():
        raise HTTPException(status_code=404, detail="Processed macro analysis is not available yet")

    try:
        processed_data = json.loads(processed_file.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.exception("[Macro] Failed to read processed data for %s", task.dapp_name)
        raise HTTPException(status_code=500, detail=f"Failed to load processed macro analysis: {exc}") from exc

    tx_order = processed_data.get("transaction_hash_list") or (processed_data.get("dapp") or {}).get("transaction_hash") or []
    transaction_summaries = [
        _summarize_transaction(tx_hash, processed_data, index)
        for index, tx_hash in enumerate(tx_order)
    ]

    return {
        "task_id": task.task_id,
        "dapp_name": task.dapp_name,
        "processed_file": _safe_relative_path(processed_file, PROCESSED_DATA_ROOT),
        "dapp": processed_data.get("dapp"),
        "transaction_hash_list": tx_order,
        "transaction_roles": processed_data.get("transaction_roles", {}),
        "attack_transactions": processed_data.get("attack_transactions", []),
        "auxiliary_transactions": processed_data.get("auxiliary_transactions", []),
        "transactions_need_analyze": processed_data.get("transactions_need_analyze", []),
        "bug_summary": processed_data.get("bug_summary", ""),
        "time_used": processed_data.get("time_used", {}),
        "token_used": processed_data.get("token_used", {}),
        "transactions": transaction_summaries,
        "balance_change": processed_data.get("balance_change", {}),
        "transaction_to_property": processed_data.get("transaction_to_property", {}),
    }


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


def _serialize_task_log(log: TaskLog, task_id: str) -> Dict[str, Any]:
    return {
        "type": "LOG",
        "task_id": task_id,
        "agent": log.agent or "Unknown",
        "level": log.level or "info",
        "message": log.message or "",
        "message_type": log.message_type or "text",
        "is_truncated": bool(log.is_truncated),
        "timestamp": log.timestamp.isoformat() if log.timestamp else datetime.now().isoformat(),
        "log_id": log.log_id,
        "persisted_id": log.id,
    }


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
            events.append(_serialize_task_log(log, task_id))
        return events
    finally:
        db_session.close()


def load_persisted_log_page(task_id: str, dapp_name: str = "", limit: int = 200, before_id: int | None = None):
    db_session = SessionLocal()
    try:
        task_ids = [task_id, dapp_name] if dapp_name else [task_id]
        base_query = db_session.query(TaskLog).filter(TaskLog.task_id.in_(task_ids))
        total = base_query.count()
        query = base_query
        if before_id is not None:
            query = query.filter(TaskLog.id < before_id)

        rows = (
            query.order_by(TaskLog.id.desc())
            .limit(limit)
            .all()
        )
        rows_chronological = list(reversed(rows))
        next_before_id = min((row.id for row in rows), default=None)
        return {
            "events": [_serialize_task_log(log, task_id) for log in rows_chronological],
            "next_before_id": next_before_id if len(rows) >= limit else None,
            "has_more": len(rows) >= limit,
            "total": total,
        }
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
    heartbeat_interval = 20.0
    heartbeat_timeout = 120.0
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
            "archived": task_snapshot.archived,
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


@app.get("/api/dapps", response_model=DappCatalogResponse)
async def list_dapps():
    if not RAW_DATA_ROOT.exists():
        raise HTTPException(status_code=404, detail="Raw DApp dataset is not available")

    items: List[DappCatalogItem] = []
    for raw_file in sorted(RAW_DATA_ROOT.glob("*.json"), key=lambda path: path.stem.lower()):
        try:
            raw_data = _load_json_file(raw_file)
        except Exception as exc:
            logger.warning("[DApps] Skipping unreadable raw file %s: %s", raw_file, exc)
            continue

        name = str(raw_data.get("name") or raw_file.stem)
        tx_hashes = raw_data.get("transaction_hash") or []
        if not isinstance(tx_hashes, list):
            tx_hashes = []

        processed_file = PROCESSED_DATA_ROOT / f"{_safe_processed_file_name(name)}.json"
        has_processed = processed_file.exists()
        demo_ready = has_processed and bool(tx_hashes)

        items.append(
            DappCatalogItem(
                name=name,
                cause=raw_data.get("cause"),
                platform=raw_data.get("platform"),
                time=raw_data.get("time"),
                root_cause=raw_data.get("root_cause"),
                report=raw_data.get("report"),
                detection=_as_reference(raw_data.get("detection")),
                disclosure=_as_reference(raw_data.get("disclosure")),
                report_link=raw_data.get("report_link"),
                transaction_hash=[str(tx_hash) for tx_hash in tx_hashes],
                transaction_count=len(tx_hashes),
                raw_file=_safe_relative_path(raw_file, RAW_DATA_ROOT),
                processed_file=_safe_relative_path(processed_file, PROCESSED_DATA_ROOT) if has_processed else None,
                has_processed_analysis=has_processed,
                demo_ready=demo_ready,
            )
        )

    items.sort(key=lambda item: (not item.demo_ready, item.name.lower()))
    return DappCatalogResponse(
        total=len(items),
        demo_ready_count=sum(1 for item in items if item.demo_ready),
        items=items,
    )


@app.get("/api/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    task_manager: TaskManager = Depends(get_task_manager),
):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.get("/api/tasks/{task_id}/macro-analysis")
async def get_macro_analysis(
    task_id: str,
    task_manager: TaskManager = Depends(get_task_manager),
):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return _load_macro_analysis_payload(task)


@app.get("/api/tasks")
async def list_tasks(
    include_archived: bool = Query(False),
    task_manager: TaskManager = Depends(get_task_manager),
):
    return await task_manager.list_tasks(include_archived=include_archived)


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


@app.get("/api/tasks/{task_id}/logs")
async def get_task_logs(
    task_id: str,
    limit: int = Query(200, ge=1, le=1000),
    before_id: int | None = Query(None),
    task_manager: TaskManager = Depends(get_task_manager),
):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return load_persisted_log_page(task_id, task.dapp_name, limit=limit, before_id=before_id)


@app.get("/api/tasks/{task_id}/automated-review")
async def get_automated_review(
    task_id: str,
    log_limit: int = Query(2000, ge=1, le=5000),
    task_manager: TaskManager = Depends(get_task_manager),
):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    logs = load_persisted_log_events(task_id, task.dapp_name, limit=log_limit)
    macro = None
    try:
        macro = _load_macro_analysis_payload(task)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise

    return build_automated_review(task, logs, macro)


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


@app.post("/api/tasks/{task_id}/archive")
async def archive_task(
    task_id: str,
    task_manager: TaskManager = Depends(get_task_manager),
):
    success = task_manager.set_task_archived(task_id, archived=True)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found or not archivable")
    return {"message": "Task archived", "archived": True}


@app.post("/api/tasks/{task_id}/unarchive")
async def unarchive_task(
    task_id: str,
    task_manager: TaskManager = Depends(get_task_manager),
):
    success = task_manager.set_task_archived(task_id, archived=False)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found or not restorable")
    return {"message": "Task restored", "archived": False}


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
    init_db()
    logger.info("[App] FastAPI server starting...")


@app.on_event("shutdown")
async def shutdown():
    logger.info("[App] FastAPI server shutting down...")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
