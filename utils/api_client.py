import asyncio
from typing import Callable, Any
import logging

import requests
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class APIClient:
    def __init__(self, apikey_bucket, max_retries=5, initial_retry_delay=1):
        self.apikey_bucket = apikey_bucket
        self.max_retries = max_retries
        self.initial_retry_delay = initial_retry_delay

    def call_with_retry(self, api_func: Callable, *args, **kwargs):
        raise NotImplementedError


class EtherscanAPIClient(APIClient):
    async def call_with_retry(self, api_func: Callable, *args, **kwargs) -> Any:
        retries = 0
        last_exception = None

        while retries < self.max_retries:
            api_key = await self.apikey_bucket.get()
            try:
                result = await asyncio.to_thread(api_func, api_key, *args, **kwargs)
                return result
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
                last_exception = e
                logger.warning(f"API call failed (attempted {retries + 1}/{self.max_retries}): {str(e)}")

                delay = self.initial_retry_delay * (2 ** retries)
                logger.info(f"Wait for {delay} seconds and then retry...")
                await asyncio.sleep(delay)
                retries += 1
        error_msg = f"All {self.max_retries} attempts of retrying have failed."
        if last_exception:
            error_msg += f": {str(last_exception)}"

        logger.error(error_msg)
        raise error_msg
