from sqlalchemy import Column, Integer, String, Text, DateTime, Enum, Boolean
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
from app.models import LogLevel, MsgType

Base = declarative_base()

class TaskLog(Base):
    __tablename__ = "task_logs"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String(36), index=True)  # UUID
    log_id = Column(String(36), unique=True, index=True)  # LogMessage.log_id
    agent = Column(String(50))
    level = Column(Enum(LogLevel))
    message_type = Column(Enum(MsgType))
    message = Column(Text)  # 摘要内容
    full_content = Column(Text)  # 完整内容（可选）
    is_truncated = Column(Boolean, default=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<TaskLog(task_id={self.task_id}, log_id={self.log_id})>"