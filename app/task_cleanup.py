import asyncio
import signal
import sys
from datetime import datetime
from typing import Dict

from .task_manager import task_manager
from .models import TaskStatus


class TaskCleanupManager:
    def __init__(self):
        self.cleanup_interval = 300  # 5分钟检查一次
        self.task_timeout = 7200  # 2小时任务超时
        self.cleanup_task = None
        
    async def start_cleanup_service(self):
        """启动清理服务"""
        print("Task cleanup service started")
        
        # 注册信号处理器，优雅地关闭清理服务
        def signal_handler(signum, frame):
            print(f"Received signal {signum}, shutting down gracefully...")
            if self.cleanup_task and not self.cleanup_task.done():
                self.cleanup_task.cancel()
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        await self._cleanup_loop()
        
    async def _cleanup_loop(self):
        """定期清理超时任务"""
        while True:
            try:
                await self.cleanup_timed_out_tasks()
                await asyncio.sleep(self.cleanup_interval)
            except asyncio.CancelledError:
                print("Task cleanup service was cancelled")
                break
            except Exception as e:
                print(f"Error during task cleanup: {e}")
    
    async def cleanup_timed_out_tasks(self):
        """清理超时的任务"""
        current_time = datetime.now()
        tasks_to_update = []
        
        for task_id, task in task_manager.tasks.items():
            if task.status == TaskStatus.RUNNING:
                # 检查任务是否超时
                time_diff = current_time - task.created_at
                if time_diff.total_seconds() > self.task_timeout:
                    tasks_to_update.append(task_id)
        
        # 更新超时任务的状态
        for task_id in tasks_to_update:
            print(f"Cleaning up timed-out task: {task_id}")
            task_manager.update_task_result(
                task_id, 
                None, 
                f"Task timed out after {self.task_timeout} seconds"
            )
            
    def set_cleanup_interval(self, interval: int):
        """设置清理间隔（秒）"""
        self.cleanup_interval = interval
        
    def set_task_timeout(self, timeout: int):
        """设置任务超时时间（秒）"""
        self.task_timeout = timeout


# 全局清理管理器实例
cleanup_manager = TaskCleanupManager()