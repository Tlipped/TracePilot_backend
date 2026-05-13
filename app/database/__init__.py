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

TASK_RUN_SCHEMA_PATCHES = {
    "archived": "ALTER TABLE task_runs ADD COLUMN archived BOOLEAN DEFAULT FALSE NOT NULL",
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
        ensure_task_run_schema()
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
        ensure_task_log_constraints()
        return

    with engine.begin() as connection:
        for ddl in missing_patches:
            connection.execute(text(ddl))
    logger.info("[DB] task_logs schema patched with %s missing columns", len(missing_patches))
    ensure_task_log_constraints()


def ensure_task_log_constraints():
    inspector = inspect(engine)
    if "task_logs" not in inspector.get_table_names():
        return

    task_id_column = next(
        (column for column in inspector.get_columns("task_logs") if column["name"] == "task_id"),
        None,
    )
    unique_task_id_constraints = [
        constraint
        for constraint in inspector.get_unique_constraints("task_logs")
        if constraint.get("column_names") == ["task_id"]
    ]

    with engine.begin() as connection:
        if task_id_column is not None:
            column_type = task_id_column.get("type")
            current_length = getattr(column_type, "length", None)
            if current_length is not None and current_length < 255:
                connection.execute(text("ALTER TABLE task_logs ALTER COLUMN task_id TYPE VARCHAR(255)"))
                logger.info("[DB] task_logs.task_id widened to VARCHAR(255)")

        for constraint in unique_task_id_constraints:
            constraint_name = constraint.get("name")
            if constraint_name:
                connection.execute(text(f'ALTER TABLE task_logs DROP CONSTRAINT "{constraint_name}"'))
                logger.info("[DB] dropped invalid unique constraint on task_logs.task_id: %s", constraint_name)


def ensure_task_run_schema():
    inspector = inspect(engine)
    if "task_runs" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("task_runs")}
    missing_patches = [
        ddl for column_name, ddl in TASK_RUN_SCHEMA_PATCHES.items()
        if column_name not in existing_columns
    ]
    if not missing_patches:
        return

    with engine.begin() as connection:
        for ddl in missing_patches:
            connection.execute(text(ddl))
    logger.info("[DB] task_runs schema patched with %s missing columns", len(missing_patches))

def get_db():
    """获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
