import asyncio
import threading
from typing import Optional
from openai import AsyncOpenAI
from settings import LLM_API_KEY, LLM_BASE_URL
from settings import LLM_MAX_CONCURRENT

DEFAULT_MAX_CONCURRENT = 5


class LLMClient:
    _instance: Optional['LLMClient'] = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super(LLMClient, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        with self._lock:
            if getattr(self, '_initialized', False):
                return

            self.client = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

            try:
                limit = int(LLM_MAX_CONCURRENT)
            except (ImportError, ValueError, TypeError):
                limit = DEFAULT_MAX_CONCURRENT

            if limit <= 0:
                limit = DEFAULT_MAX_CONCURRENT
            self.semaphore = asyncio.Semaphore(limit)

            self._initialized = True
            print(f"✅ [LLMClient] Initialized (PID: {id(self)}) with max_concurrent={limit}")

    async def create_chat_completion(self, *args, **kwargs):
        if not hasattr(self, 'semaphore'):
            raise RuntimeError("LLMClient not properly initialized")

        async with self.semaphore:
            try:
                return await self.client.chat.completions.create(*args, **kwargs)
            except Exception as e:
                raise e