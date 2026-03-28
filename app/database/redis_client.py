import redis
from urllib.parse import urlparse
import os
from dotenv import load_dotenv

load_dotenv()

# 从环境变量获取 Redis URL
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

class RedisClient:
    def __init__(self):
        self.client = redis.from_url(REDIS_URL)

    def set_log(self, log_id: str, content: str, expire: int = 3600):
        self.client.setex(f"log:{log_id}", expire, content)

    def get_log(self, log_id: str) -> str:
        result = self.client.get(f"log:{log_id}")
        return result.decode('utf-8') if result else None

    def delete_log(self, log_id: str):
        self.client.delete(f"log:{log_id}")

# 单例模式
redis_client = RedisClient()