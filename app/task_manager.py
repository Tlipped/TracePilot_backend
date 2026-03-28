import asyncio
import uuid
import json
import sys
import os
import concurrent.futures
from datetime import datetime
from typing import Dict, Optional, Any, List
from .models import TaskStatus, TaskResponse, LogMessage
# 修复导入路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class TaskManager:
    def __init__(self, max_concurrent_tasks=3):
        self.tasks: Dict[str, TaskResponse] = {}
        self.task_queues: Dict[str, asyncio.Queue] = {}
        self.running_tasks: Dict[str, asyncio.Future] = {} 
        self.max_concurrent_tasks = max_concurrent_tasks
        # 核心：使用线程池来隔离阻塞的工作流
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_concurrent_tasks,
            thread_name_prefix="WorkflowThread"
        )

    def create_task(self, dapp_name: str) -> str:
        """创建任务并返回 task_id"""
        task_id = str(uuid.uuid4())
        self.tasks[task_id] = TaskResponse(
            task_id=task_id,
            dapp_name=dapp_name,
            status=TaskStatus.PENDING,
            created_at=datetime.now()
        )
        self.task_queues[task_id] = asyncio.Queue()
        return task_id

    def update_task_status(self, task_id: str, status: TaskStatus):
        """更新任务状态"""
        if task_id in self.tasks:
            self.tasks[task_id].status = status

    def update_task_result(self, task_id: str, result: Any, error: Optional[str] = None):
        """更新任务结果"""
        if task_id in self.tasks:
            self.tasks[task_id].result = result
            self.tasks[task_id].completed_at = datetime.now()
            self.tasks[task_id].status = TaskStatus.FAILED if error else TaskStatus.COMPLETED
            self.tasks[task_id].error = error

    def _sync_workflow_wrapper(self, task_id: str, dapp_name: str, loop: asyncio.AbstractEventLoop):
        """
        在独立线程中运行的包装器
        """
        from main import WorkFlow
        try:
            # 1. 更新状态为运行中 (线程安全)
            loop.call_soon_threadsafe(self.update_task_status, task_id, TaskStatus.RUNNING)

            # 2. 定义桥接日志回调
            def thread_safe_log_callback(log_msg: LogMessage):
                """接收 LogMessage 对象，转为字典后投递给主线程"""
                try:
                    # ✅ 改进：优先用 v2 的 model_dump，降级到 v1 的 dict
                    if hasattr(log_msg, 'model_dump'):
                        log_dict = log_msg.model_dump()
                    else:
                        log_dict = log_msg.dict()
                    
                    # 确保消息包含必要的字段
                    log_dict.setdefault('type', 'LOG')
                    
                    loop.call_soon_threadsafe(
                        self.task_queues[task_id].put_nowait, 
                        log_dict
                    )
                except Exception as e:
                    print(f"[TaskManager] Failed to serialize log message: {e}")

            # 3. 在子线程中启动一个新的事件循环来处理 WorkFlow 的 async 方法
            # 这是因为 WorkFlow 内部大量使用了 await
            async def run_async_part():
                workflow = WorkFlow(semaphore_num=1, log_callback=thread_safe_log_callback)
                
                # 读取数据
                dapps = []
                for fn in workflow.raw_file_names:
                    if not fn.endswith('.json'): 
                        continue
                    with open(fn, 'r', encoding="utf-8") as f:
                        dapps.append(json.load(f))
                
                target_dapp = next((d for d in dapps if d["name"] == dapp_name), None)
                if not target_dapp:
                    return None, f"DApp '{dapp_name}' not found"
                
                target_index = next(i for i, d in enumerate(dapps) if d["name"] == dapp_name)
                
                # 执行
                return await workflow.process_single_dapp(target_dapp, target_index), None

            # 运行子循环
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            result, error = new_loop.run_until_complete(run_async_part())
            new_loop.close()

            # 4. 回写结果 (线程安全)
            loop.call_soon_threadsafe(self.update_task_result, task_id, result, error)

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            loop.call_soon_threadsafe(self.update_task_result, task_id, None, f"{str(e)}\n{error_details}")

    def start_task(self, task_id: str, dapp_name: str):
        """
        启动任务：直接交给线程池，主线程立即返回
        """
        if task_id in self.tasks:
            main_loop = asyncio.get_running_loop()
            # 使用 run_in_executor 彻底不占用主线程的时间片
            fut = main_loop.run_in_executor(
                self.executor, 
                self._sync_workflow_wrapper, 
                task_id, 
                dapp_name, 
                main_loop
            )
            self.running_tasks[task_id] = fut
            return fut

    def get_task(self, task_id: str) -> Optional[TaskResponse]:
        """获取单个任务"""
        return self.tasks.get(task_id)

    async def get_task_async(self, task_id: str) -> Optional[TaskResponse]:
        """异步获取单个任务（供 API 调用）"""
        return self.tasks.get(task_id)

    def get_all_tasks(self) -> List[TaskResponse]:
        """获取所有任务列表"""
        return list(self.tasks.values())

    async def list_tasks(self) -> List[TaskResponse]:
        """异步获取所有任务列表（供 API 调用）"""
        return self.get_all_tasks()

    def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        if task_id in self.running_tasks:
            # 线程池中的任务较难强行 Kill，这里标记为取消
            self.running_tasks[task_id].cancel()
            self.update_task_result(task_id, None, "Task was cancelled")
            return True
        return False

    def get_task_queue(self, task_id: str) -> Optional[asyncio.Queue]:
        """✅ 改进：安全获取任务队列"""
        if task_id in self.task_queues:
            return self.task_queues[task_id]
        return None


_task_manager = None

def get_task_manager() -> TaskManager:
    """获取全局任务管理器实例"""
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager
# task_manager.py 中新增
# import sqlite3
# from pathlib import Path
# from datetime import datetime
# import json
# import os

# class TaskDB:
#     def __init__(self, db_path: str = "tasks.db"):
#         self.db_path = db_path
#         self.init_db()

#     def init_db(self):
#         conn = sqlite3.connect(self.db_path)
#         cursor = conn.cursor()
#         cursor.execute('''
#             CREATE TABLE IF NOT EXISTS tasks (
#                 task_id TEXT PRIMARY KEY,
#                 dapp_name TEXT NOT NULL,
#                 status TEXT NOT NULL,
#                 result TEXT,
#                 error TEXT,
#                 created_at TEXT NOT NULL,
#                 completed_at TEXT,
#                 final_report TEXT
#             )
#         ''')
#         conn.commit()
#         conn.close()

#     def save_task(self, task_id: str, task_data: dict):
#         conn = sqlite3.connect(self.db_path)
#         cursor = conn.cursor()
#         cursor.execute('''
#             INSERT OR REPLACE INTO tasks 
#             (task_id, dapp_name, status, result, error, created_at, completed_at, final_report)
#             VALUES (?, ?, ?, ?, ?, ?, ?, ?)
#         ''', (
#             task_data['task_id'],
#             task_data['dapp_name'],
#             task_data['status'],
#             json.dumps(task_data.get('result'), ensure_ascii=False),
#             task_data.get('error'),
#             task_data['created_at'].isoformat(),
#             task_data.get('completed_at') and task_data['completed_at'].isoformat(),
#             task_data.get('final_report')
#         ))
#         conn.commit()
#         conn.close()

#     def load_all_tasks(self) -> list:
#         conn = sqlite3.connect(self.db_path)
#         cursor = conn.cursor()
#         cursor.execute('SELECT * FROM tasks ORDER BY created_at DESC')
#         rows = cursor.fetchall()
#         conn.close()

#         tasks = []
#         for row in rows:
#             task = {
#                 'task_id': row[0],
#                 'dapp_name': row[1],
#                 'status': row[2],
#                 'result': json.loads(row[3]) if row[3] else None,
#                 'error': row[4],
#                 'created_at': datetime.fromisoformat(row[5]),
#                 'completed_at': datetime.fromisoformat(row[6]) if row[6] else None,
#                 'final_report': row[7]
#             }
#             tasks.append(task)
#         return tasks

#     def get_task(self, task_id: str) -> Optional[dict]:
#         conn = sqlite3.connect(self.db_path)
#         cursor = conn.cursor()
#         cursor.execute('SELECT * FROM tasks WHERE task_id = ?', (task_id,))
#         row = cursor.fetchone()
#         conn.close()

#         if not row:
#             return None

#         return {
#             'task_id': row[0],
#             'dapp_name': row[1],
#             'status': row[2],
#             'result': json.loads(row[3]) if row[3] else None,
#             'error': row[4],
#             'created_at': datetime.fromisoformat(row[5]),
#             'completed_at': datetime.fromisoformat(row[6]) if row[6] else None,
#             'final_report': row[7]
#         }
# 单例
# task_manager = TaskManager(max_concurrent_tasks=5)