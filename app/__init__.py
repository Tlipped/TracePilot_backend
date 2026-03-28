import logging
from app.database import init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 应用启动时初始化数据库
try:
    if init_db():
        logger.info("[App] Database initialized successfully")
    else:
        logger.warning("[App] Database initialization encountered issues")
except Exception as e:
    logger.error(f"[App] Critical database initialization error: {e}")