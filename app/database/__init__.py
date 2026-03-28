from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv
import logging

load_dotenv()

logger = logging.getLogger(__name__)

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
        logger.info("[DB] Database tables created/verified successfully")
        return True
    except Exception as e:
        logger.error(f"[DB] Failed to initialize database: {e}")
        return False

def get_db():
    """获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()