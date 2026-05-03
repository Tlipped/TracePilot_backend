"""
TracePilot Web API Package
"""
from pydantic import BaseModel,Field
from typing import Optional, Dict, Any
from enum import Enum
import uuid
from datetime import datetime


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskCreateRequest(BaseModel):
    dapp_name: str


class TaskResponse(BaseModel):
    task_id: str
    dapp_name: str
    status: TaskStatus
    created_at: datetime
    completed_at: Optional[datetime] = None
    duration: Optional[float] = None  # 耗时（秒）
    final_report: Optional[str] = None # 最终生成的 Markdown 报告内容
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    archived: bool = False

class LogLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    DEBUG = "debug"

class MsgType(str, Enum):
    TEXT = "text"          # 纯文本
    MARKDOWN = "markdown"  # 格式化内容
    TOOL_CALL = "tool"     # 工具调用
    RESULT = "result"      # 最终结果

# class LogMessage(BaseModel):
#     agent: str
#     level: LogLevel
#     message_type: MsgType
#     message: str
#     is_truncated: bool = False
#     timestamp: Optional[str] = None
class LogMessage(BaseModel):
    agent: str
    level: LogLevel
    message_type: MsgType
    message: str
    is_truncated: bool = False
    timestamp: Optional[str] = None
    log_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
