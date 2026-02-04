import datetime
import ssl
from typing import Dict

import aiohttp
import httpx


class Downloader:
    async def download(self, *args, **kwargs):
        result = await self._preprocess(*args, **kwargs)
        if result is not None:
            return result
        result = await self._fetch(*args, **kwargs)
        return await self._process(result, **kwargs)

    async def _preprocess(self, *args, **kwargs):
        raise NotImplemented()

    async def _fetch(self, *args, **kwargs):
        raise NotImplemented()

    async def _process(self, result, *args, **kwargs):
        raise NotImplemented()


class JSONRPCDownloader(Downloader):
    def __init__(self, rpc_url: str):
        self.rpc_url = rpc_url

    def get_request_param(self, *args, **kwargs) -> Dict:
        raise NotImplemented()

    async def _fetch(self, *args, **kwargs):
        params = self.get_request_param(*args, **kwargs)
        timeout = aiohttp.ClientTimeout(total=180)
        client = aiohttp.ClientSession(timeout=timeout, trust_env=True)
        async with client.post(**params) as response:
            rlt = await response.text()
        await client.close()
        return rlt


class EtherscanDownloader(Downloader):
    def __init__(self, apikey: str):
        self.apikey = apikey
        self._client = None

    def get_request_param(self, *args, **kwargs) -> Dict:
        raise NotImplemented()

    async def get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                verify=False,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                }
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _fetch(self, *args, **kwargs):
        client = await self.get_client()
        params = self.get_request_param(*args, **kwargs)
        try:
            response = await client.get(params['url'])
            response.raise_for_status()
            return response.text
        except Exception as e:
            print(f"error: {e}")
            raise
