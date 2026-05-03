import asyncio
import concurrent.futures
import json
import os
import sys
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .models import LogMessage, TaskResponse, TaskStatus
from app.database import SessionLocal
from app.database.models import TaskRun

# Ensure project root is importable when task workers load `main.WorkFlow`.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TaskManager:
    EVENT_QUEUE_MAXSIZE = 5000
    LOG_DROP_NOTICE_THRESHOLD = 50

    def __init__(self, max_concurrent_tasks: int = 3):
        self.tasks: Dict[str, TaskResponse] = {}
        self.task_queues: Dict[str, asyncio.Queue] = {}
        self.running_tasks: Dict[str, asyncio.Future] = {}
        self.cancel_events: Dict[str, threading.Event] = {}
        self.dropped_logs: Dict[str, int] = {}
        self.max_concurrent_tasks = max_concurrent_tasks
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_concurrent_tasks,
            thread_name_prefix="WorkflowThread",
        )
        self._load_persisted_tasks()

    def create_task(self, dapp_name: str) -> str:
        task_id = str(uuid.uuid4())
        task = TaskResponse(
            task_id=task_id,
            dapp_name=dapp_name,
            status=TaskStatus.PENDING,
            created_at=datetime.now(),
        )
        self.tasks[task_id] = task
        self.task_queues[task_id] = asyncio.Queue(maxsize=self.EVENT_QUEUE_MAXSIZE)
        self.cancel_events[task_id] = threading.Event()
        self.dropped_logs[task_id] = 0
        self._persist_task(task)
        self._emit_task_status(task_id)
        return task_id

    def update_task_status(self, task_id: str, status: TaskStatus):
        task = self.tasks.get(task_id)
        if not task:
            return
        if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            return
        task.status = status
        self._persist_task(task)
        self._emit_task_status(task_id)

    def update_task_result(self, task_id: str, result: Any, error: Optional[str] = None):
        task = self.tasks.get(task_id)
        if not task:
            return

        if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            return

        normalized_result, final_report = self._normalize_result(result)
        task.result = normalized_result
        task.final_report = final_report
        task.completed_at = datetime.now()
        task.duration = max((task.completed_at - task.created_at).total_seconds(), 0.0)
        task.status = TaskStatus.FAILED if error else TaskStatus.COMPLETED
        task.error = error
        self.running_tasks.pop(task_id, None)

        self._persist_task(task)
        self._emit_task_status(task_id)
        self._enqueue_task_message(
            task_id,
            {
                "type": "TASK_FINAL",
                "task_id": task_id,
                "dapp_name": task.dapp_name,
                "status": task.status.value,
                "completed_at": task.completed_at.isoformat() if task.completed_at else None,
                "duration": task.duration,
                "final_report": task.final_report,
                "error": task.error,
            },
        )

    def _sync_workflow_wrapper(self, task_id: str, dapp_name: str, loop: asyncio.AbstractEventLoop):
        from main import WorkFlow

        try:
            loop.call_soon_threadsafe(self.update_task_status, task_id, TaskStatus.RUNNING)
            cancel_event = self.cancel_events.get(task_id)

            def thread_safe_log_callback(log_msg: LogMessage):
                try:
                    log_dict = log_msg.model_dump() if hasattr(log_msg, "model_dump") else log_msg.dict()
                    log_dict["type"] = "LOG"
                    loop.call_soon_threadsafe(self._enqueue_task_message, task_id, log_dict)
                except Exception as exc:
                    print(f"[TaskManager] Failed to serialize/enqueue log: {exc}")

            async def run_async_part() -> Tuple[Any, Optional[str]]:
                workflow = WorkFlow(
                    semaphore_num=1,
                    log_callback=thread_safe_log_callback,
                    cancellation_checker=lambda: bool(cancel_event and cancel_event.is_set()),
                )
                dapps = []
                for fn in workflow.raw_file_names:
                    if not fn.endswith(".json"):
                        continue
                    with open(fn, "r", encoding="utf-8") as f:
                        dapps.append(json.load(f))

                target_dapp = next((d for d in dapps if d["name"] == dapp_name), None)
                if not target_dapp:
                    return None, f"DApp '{dapp_name}' not found"

                if cancel_event and cancel_event.is_set():
                    raise asyncio.CancelledError()

                target_index = next(i for i, d in enumerate(dapps) if d["name"] == dapp_name)
                return await workflow.process_single_dapp(target_dapp, target_index), None

            new_loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(new_loop)
                result, error = new_loop.run_until_complete(run_async_part())
            finally:
                new_loop.close()

            loop.call_soon_threadsafe(self.update_task_result, task_id, result, error)

        except asyncio.CancelledError:
            loop.call_soon_threadsafe(self.update_task_result, task_id, None, "Task was cancelled")
        except Exception as exc:
            import traceback

            error_details = traceback.format_exc()
            loop.call_soon_threadsafe(
                self.update_task_result,
                task_id,
                None,
                f"{str(exc)}\n{error_details}",
            )
        finally:
            loop.call_soon_threadsafe(self.cancel_events.pop, task_id, None)

    def start_task(self, task_id: str, dapp_name: str):
        if task_id not in self.tasks:
            return None

        main_loop = asyncio.get_running_loop()
        fut = main_loop.run_in_executor(
            self.executor,
            self._sync_workflow_wrapper,
            task_id,
            dapp_name,
            main_loop,
        )
        self.running_tasks[task_id] = fut
        return fut

    def get_task(self, task_id: str) -> Optional[TaskResponse]:
        return self.tasks.get(task_id)

    async def get_task_async(self, task_id: str) -> Optional[TaskResponse]:
        return self.tasks.get(task_id)

    def get_all_tasks(self) -> List[TaskResponse]:
        return list(self.tasks.values())

    async def list_tasks(self) -> List[TaskResponse]:
        return self.get_all_tasks()

    def _load_persisted_tasks(self):
        db = SessionLocal()
        try:
            db_tasks = db.query(TaskRun).order_by(TaskRun.created_at.desc()).all()
            for db_task in db_tasks:
                status_value = db_task.status or TaskStatus.FAILED.value
                error = db_task.error
                completed_at = db_task.completed_at
                if status_value in {TaskStatus.PENDING.value, TaskStatus.RUNNING.value}:
                    status_value = TaskStatus.FAILED.value
                    error = error or "Task was interrupted because the backend process stopped"
                    completed_at = completed_at or datetime.now()
                    db_task.status = status_value
                    db_task.error = error
                    db_task.completed_at = completed_at
                    db_task.updated_at = datetime.now()

                result = None
                if db_task.result:
                    try:
                        result = json.loads(db_task.result)
                    except Exception:
                        result = {"raw": db_task.result}

                created_at = db_task.created_at or datetime.now()
                duration = None
                if completed_at:
                    duration = max((completed_at - created_at).total_seconds(), 0.0)

                task = TaskResponse(
                    task_id=db_task.task_id,
                    dapp_name=db_task.dapp_name,
                    status=TaskStatus(status_value),
                    created_at=created_at,
                    completed_at=completed_at,
                    duration=duration,
                    final_report=db_task.final_report,
                    result=result,
                    error=error,
                )
                self.tasks[task.task_id] = task
                self.task_queues[task.task_id] = asyncio.Queue(maxsize=self.EVENT_QUEUE_MAXSIZE)
                self.dropped_logs[task.task_id] = 0

            db.commit()
        except Exception as exc:
            db.rollback()
            print(f"[TaskManager] Failed to load persisted tasks: {exc}")
        finally:
            db.close()

    def cancel_task(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task or task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            return False

        cancel_event = self.cancel_events.get(task_id)
        if cancel_event:
            cancel_event.set()

        task_future = self.running_tasks.get(task_id)
        if task_future:
            task_future.cancel()

        self.update_task_result(task_id, None, "Task was cancelled")
        return True

    def get_task_queue(self, task_id: str) -> Optional[asyncio.Queue]:
        return self.task_queues.get(task_id)

    def _emit_task_status(self, task_id: str):
        task = self.tasks.get(task_id)
        if not task:
            return
        self._enqueue_task_message(
            task_id,
            {
                "type": "TASK_STATUS",
                "task_id": task.task_id,
                "dapp_name": task.dapp_name,
                "status": task.status.value,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "completed_at": task.completed_at.isoformat() if task.completed_at else None,
                "duration": task.duration,
                "error": task.error,
            },
        )

    def _enqueue_task_message(self, task_id: str, payload: Dict[str, Any]):
        queue = self.task_queues.get(task_id)
        if not queue:
            return

        msg_type = payload.get("type")
        if msg_type == "LOG":
            if queue.full():
                dropped = self.dropped_logs.get(task_id, 0) + 1
                self.dropped_logs[task_id] = dropped
                if dropped % self.LOG_DROP_NOTICE_THRESHOLD == 0:
                    self._enqueue_control_message(
                        queue,
                        {
                            "type": "LOG_DROPPED",
                            "task_id": task_id,
                            "count": dropped,
                            "message": "Log throughput is high; partial log messages were dropped.",
                            "timestamp": datetime.now().isoformat(),
                        },
                    )
                return
            queue.put_nowait(payload)
            return

        self._flush_drop_notice(task_id, queue)
        self._enqueue_control_message(queue, payload)

    def _flush_drop_notice(self, task_id: str, queue: asyncio.Queue):
        dropped = self.dropped_logs.get(task_id, 0)
        if dropped <= 0:
            return
        self.dropped_logs[task_id] = 0
        self._enqueue_control_message(
            queue,
            {
                "type": "LOG_DROPPED",
                "task_id": task_id,
                "count": dropped,
                "message": "Recovered from temporary backpressure.",
                "timestamp": datetime.now().isoformat(),
            },
        )

    @staticmethod
    def _drop_one_log(queue: asyncio.Queue) -> bool:
        if queue.empty():
            return False

        size = queue.qsize()
        kept_items: List[Dict[str, Any]] = []
        dropped = False

        for _ in range(size):
            item = queue.get_nowait()
            if not dropped and isinstance(item, dict) and item.get("type") == "LOG":
                dropped = True
                continue
            kept_items.append(item)

        for item in kept_items:
            queue.put_nowait(item)
        return dropped

    def _enqueue_control_message(self, queue: asyncio.Queue, payload: Dict[str, Any]):
        if queue.full():
            dropped_log = self._drop_one_log(queue)
            if not dropped_log and queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
        if not queue.full():
            queue.put_nowait(payload)

    @staticmethod
    def _normalize_result(result: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if result is None:
            return None, None

        if isinstance(result, tuple):
            normalized: Dict[str, Any] = {}
            if len(result) > 0:
                normalized["dapp_name"] = result[0]
            if len(result) > 1:
                normalized["final_report"] = result[1]
            if len(result) > 2:
                normalized["extra"] = list(result[2:])
            final_report = result[1] if len(result) > 1 and isinstance(result[1], str) else None
            return normalized, final_report

        if isinstance(result, dict):
            final_report_value = result.get("final_report", result.get("report"))
            if isinstance(final_report_value, str):
                return result, final_report_value
            if final_report_value is None:
                return result, None
            return result, json.dumps(final_report_value, ensure_ascii=False, default=str)

        if isinstance(result, str):
            return {"final_report": result}, result

        return {"value": str(result)}, None

    @staticmethod
    def _serialize_result(result: Optional[Dict[str, Any]]) -> Optional[str]:
        if result is None:
            return None
        try:
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception:
            return json.dumps({"raw": str(result)}, ensure_ascii=False)

    def _persist_task(self, task: TaskResponse):
        db = SessionLocal()
        try:
            db_task = db.query(TaskRun).filter(TaskRun.task_id == task.task_id).first()
            if not db_task:
                db_task = TaskRun(task_id=task.task_id, dapp_name=task.dapp_name)
                db.add(db_task)

            db_task.status = task.status.value if hasattr(task.status, "value") else str(task.status)
            db_task.final_report = task.final_report
            db_task.result = self._serialize_result(task.result)
            db_task.error = task.error
            db_task.created_at = task.created_at
            db_task.completed_at = task.completed_at
            db_task.updated_at = datetime.now()

            db.commit()
        except Exception as exc:
            db.rollback()
            print(f"[TaskManager] Failed to persist task {task.task_id}: {exc}")
        finally:
            db.close()


_task_manager: Optional[TaskManager] = None


def get_task_manager() -> TaskManager:
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager


# Backward compatibility for old imports (`from app.task_manager import task_manager`)
task_manager = get_task_manager()
