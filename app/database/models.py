from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class TaskLog(Base):
    __tablename__ = "task_logs"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String(255), nullable=False, index=True)
    log_id = Column(String(36), nullable=False, index=True)
    agent = Column(String(128), index=True)
    level = Column(String(32), index=True)
    message_type = Column(String(32), index=True)
    message = Column(Text)
    full_content = Column(Text)
    is_truncated = Column(Boolean, default=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<TaskLog(task_id={self.task_id}, agent={self.agent}, log_id={self.log_id})>"


class TaskRun(Base):
    __tablename__ = "task_runs"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String(36), unique=True, index=True)
    dapp_name = Column(String(255), nullable=False, index=True)
    status = Column(String(32), nullable=False, index=True)
    archived = Column(Boolean, default=False, nullable=False, index=True)

    final_report = Column(Text)
    result = Column(Text)
    error = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<TaskRun(task_id={self.task_id}, status={self.status})>"
