"""
TracePilot Web API Package
"""
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
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


class DappReference(BaseModel):
    time: Optional[str] = None
    link: Optional[str] = None


class DappCatalogItem(BaseModel):
    name: str
    cause: Optional[str] = None
    platform: Optional[str] = None
    time: Optional[str] = None
    root_cause: Optional[str] = None
    report: Optional[str] = None
    detection: Optional[DappReference] = None
    disclosure: Optional[DappReference] = None
    report_link: Optional[str] = None
    transaction_hash: List[str] = Field(default_factory=list)
    transaction_count: int = 0
    raw_file: str
    processed_file: Optional[str] = None
    has_processed_analysis: bool = False
    demo_ready: bool = False


class DappCatalogResponse(BaseModel):
    total: int
    demo_ready_count: int
    items: List[DappCatalogItem] = Field(default_factory=list)


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
