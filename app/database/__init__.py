from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv
import logging

load_dotenv()

logger = logging.getLogger(__name__)

TASK_LOG_SCHEMA_PATCHES = {
    "agent": "ALTER TABLE task_logs ADD COLUMN agent VARCHAR(128)",
    "level": "ALTER TABLE task_logs ADD COLUMN level VARCHAR(32)",
    "message_type": "ALTER TABLE task_logs ADD COLUMN message_type VARCHAR(32)",
    "message": "ALTER TABLE task_logs ADD COLUMN message TEXT",
    "full_content": "ALTER TABLE task_logs ADD COLUMN full_content TEXT",
}

DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql://tracepilot_user:tracepilot_pass@localhost:5432/tracepilot"
)

try:
    engine = create_engine(
        DATABASE_URL,
        echo=False,
        pool_pre_ping=True,  # 连接前检查连接是否有效
        pool_recycle=3600,   # 1小时后回收连接
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    logger.info(f"[DB] Connected to {DATABASE_URL}")
except Exception as e:
    logger.error(f"[DB] Failed to connect: {e}")
    raise

def init_db():
    """初始化数据库表（自动创建所有表）"""
    try:
        from .models import Base
        
        # 创建所有表
        Base.metadata.create_all(bind=engine)
        ensure_task_log_schema()
        logger.info("[DB] Database tables created/verified successfully")
        return True
    except Exception as e:
        logger.error(f"[DB] Failed to initialize database: {e}")
        return False


def ensure_task_log_schema():
    inspector = inspect(engine)
    if "task_logs" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("task_logs")}
    missing_patches = [
        ddl for column_name, ddl in TASK_LOG_SCHEMA_PATCHES.items()
        if column_name not in existing_columns
    ]
    if not missing_patches:
        return

    with engine.begin() as connection:
        for ddl in missing_patches:
            connection.execute(text(ddl))
    logger.info("[DB] task_logs schema patched with %s missing columns", len(missing_patches))

def get_db():
    """获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
