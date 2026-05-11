import json
import os
from typing import Dict

import aiohttp

from downloaders.defs import JSONRPCDownloader, Downloader
from settings import PC_TRACER, CACHE_DIR, PHALCON_TRACER, ACCOUNT_SLUG, PROJECT_SLUG, TENDERLY_API_KEY


class PCTraceDownloader(JSONRPCDownloader):
    def get_request_param(self, transaction_hash: str) -> Dict:
        data = {
            "id": 1,
            "jsonrpc": "2.0",
            "params": [
                transaction_hash.lower(),
                {"tracer": PC_TRACER},
            ],
            "method": "debug_traceTransaction"
        }
        return {
            "url": self.rpc_url,
            "json": data,
        }

    async def _preprocess(self, transaction_hash: str):
        path = os.path.join(CACHE_DIR, 'pc', '%s.json' % transaction_hash)
        if not os.path.exists(path):
            return None
        with open(path, 'r') as f:
            return json.load(f)

    async def _process(self, result: str, **kwargs):
        result = json.loads(result)
        result = [item for item in result["result"]]

        # cache data
        transaction_hash = kwargs['transaction_hash']
        path = os.path.join(CACHE_DIR, 'pc')
        if not os.path.exists(path):
            os.makedirs(path)
        path = os.path.join(path, '%s.json' % transaction_hash)
        with open(path, 'w') as f:
            json.dump(result, f)
        return result


class FlatTraceDownloader(JSONRPCDownloader):
    def get_request_param(self, transaction_hash: str) -> Dict:
        data = {
            "id": 1,
            "jsonrpc": "2.0",
            "params": [
                transaction_hash.lower(),
                {"tracer": "callTracer"},
            ],
            "method": "debug_traceTransaction"
        }
        return {
            "url": self.rpc_url,
            "json": data,
        }

    async def _preprocess(self, transaction_hash: str):
        path = os.path.join(CACHE_DIR, 'trace/flat_trace', '%s.json' % transaction_hash)
        if not os.path.exists(path):
            return None
        with open(path, 'r') as f:
            return json.load(f)

    async def _process(self, result: str, **kwargs):
        result = json.loads(result)
        result = [item for item in self._parse(result['result'])]

        # cache data
        transaction_hash = kwargs['transaction_hash']
        path = os.path.join(CACHE_DIR, 'trace/flat_trace')
        if not os.path.exists(path):
            os.makedirs(path)
        path = os.path.join(path, '%s.json' % transaction_hash)
        with open(path, 'w') as f:
            json.dump(result, f)
        return result

    def _parse(self, data: dict):
        if not data.get('calls'):
            yield data
            return

        calls = data.pop('calls')
        yield data
        for _call in calls:
            yield from self._parse(_call)


class RawTraceDownloader(JSONRPCDownloader):
    def get_request_param(self, transaction_hash: str) -> Dict:
        data = {
            "id": 1,
            "jsonrpc": "2.0",
            "params": [
                transaction_hash.lower(),
                {"tracer": "callTracer"},
            ],
            "method": "debug_traceTransaction"
        }
        return {
            "url": self.rpc_url,
            "json": data,
        }

    async def _preprocess(self, transaction_hash: str):
        path = os.path.join(CACHE_DIR, 'trace/raw_trace', '%s.json' % transaction_hash)
        if not os.path.exists(path):
            return None
        with open(path, 'r') as f:
            return json.load(f)

    async def _process(self, result: str, **kwargs):
        result = json.loads(result)

        # cache data
        transaction_hash = kwargs['transaction_hash']
        path = os.path.join(CACHE_DIR, 'trace/raw_trace')
        if not os.path.exists(path):
            os.makedirs(path)
        path = os.path.join(path, '%s.json' % transaction_hash)
        with open(path, 'w') as f:
            json.dump(result, f)
        return result


class PhalconTraceDownloader(JSONRPCDownloader):
    def get_request_param(self, transaction_hash: str) -> Dict:
        data = {
            "id": 1,
            "jsonrpc": "2.0",
            "params": [
                transaction_hash.lower(),
                {
                    "tracer": PHALCON_TRACER
                },
            ],
            "method": "debug_traceTransaction"
        }
        return {
            "url": self.rpc_url,
            "json": data,
        }

    async def _preprocess(self, transaction_hash: str):
        path = os.path.join(CACHE_DIR, 'trace/phalcon_trace', '%s.json' % transaction_hash)
        if not os.path.exists(path):
            return None
        with open(path, 'r') as f:
            return json.load(f)

    async def _process(self, result: str, **kwargs):
        parsed = json.loads(result)

        if 'error' in parsed:
            print(f"RPC Error for {kwargs.get('transaction_hash')}: {parsed['error']}")
            return {}

        result_data = parsed.get('result', {})

        transaction_hash = kwargs['transaction_hash']
        path = os.path.join(CACHE_DIR, 'trace/phalcon_trace')
        if not os.path.exists(path):
            os.makedirs(path)
        path = os.path.join(path, '%s.json' % transaction_hash)
        with open(path, 'w') as f:
            json.dump(result_data, f)

        return result_data


class TenderlySimulateDownloader(Downloader):
    def get_request_param(self, payload: Dict) -> Dict:
        return {
            "url": f"https://api.tenderly.co/api/v1/account/{ACCOUNT_SLUG}/project/{PROJECT_SLUG}/simulate",
            "headers": {
                "X-Access-Key": TENDERLY_API_KEY,
                "Content-Type": "application/json",
            },
            "json": payload,
        }

    async def _preprocess(self, payload: Dict, **kwargs):
        if payload.get("cache", True):
            transaction_hash = payload["transaction_hash"]
            path = os.path.join(CACHE_DIR, 'trace/simulation_id', f'{transaction_hash.lower()}.json')
            if not os.path.exists(path):
                return None
            with open(path, 'r', encoding='utf-8') as f:
                wrapped_id = json.load(f)
                return wrapped_id["simulation_id"]

    async def _fetch(self, payload: Dict, **kwargs):
        params = self.get_request_param(payload)
        timeout = aiohttp.ClientTimeout(total=300, sock_connect=30, sock_read=300)

        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.post(params['url'], headers=params['headers'], json=params['json']) as response:
                rlt = await response.text()
                if response.status >= 400:
                    print(f"Tenderly API warning (HTTP {response.status}): {rlt}")
                return rlt

    async def _process(self, result_text: str, **kwargs):
        try:
            result = json.loads(result_text)
        except Exception as e:
            print(f"JSON parse error: {e}")
            return None

        simulation = result.get('simulation', {})
        simulation_id = simulation.get('id', -1)
        if simulation_id == -1:
            raise Exception("simulation error!")

        payload = kwargs.get('payload')
        if payload.get("cache", True):
            transaction_hash = payload["transaction_hash"]
            path = os.path.join(CACHE_DIR, 'trace/simulation_id')
            if not os.path.exists(path):
                os.makedirs(path)
            cache_path = os.path.join(path, f'{transaction_hash.lower()}.json')
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump({"simulation_id": simulation_id}, f, indent=4)
        return simulation_id


class TenderlyFullSimulationDownloader(Downloader):
    def get_request_param(self, simulation_id: str) -> Dict:
        return {
            "url": f"https://api.tenderly.co/api/v1/account/{ACCOUNT_SLUG}/project/{PROJECT_SLUG}/simulations/{simulation_id}",
            "headers": {
                "X-Access-Key": TENDERLY_API_KEY,
                "Content-Type": "application/json",
            }
        }

    async def _preprocess(self, simulation_id: str, **kwargs):
        path = os.path.join(CACHE_DIR, 'trace/simulation_result', f'{simulation_id.lower()}.json')
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    async def _fetch(self, simulation_id: str, **kwargs):
        params = self.get_request_param(simulation_id)
        timeout = aiohttp.ClientTimeout(total=300, sock_connect=30, sock_read=300)

        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.post(params['url'], headers=params['headers']) as response:
                rlt = await response.text()
                if response.status >= 400:
                    print(f"Tenderly API 警告 (HTTP {response.status}): {rlt}")
                return rlt

    async def _process(self, result_text: str, **kwargs):
        try:
            result = json.loads(result_text)
        except Exception as e:
            print(f"JSON parse error: {e}")
            return None

        simulation_id = kwargs.get('simulation_id')
        path = os.path.join(CACHE_DIR, 'trace/simulation_result')
        if not os.path.exists(path):
            os.makedirs(path)

        cache_path = os.path.join(path, f'{simulation_id.lower()}.json')
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=4)

        return result


class TenderlyBundleSimulateDownloader(Downloader):
    def get_request_param(self, payload: Dict) -> Dict:
        return {
            "url": f"https://api.tenderly.co/api/v1/account/{ACCOUNT_SLUG}/project/{PROJECT_SLUG}/simulate-bundle",
            "headers": {
                "X-Access-Key": TENDERLY_API_KEY,
                "Content-Type": "application/json",
            },
            "json": payload,
        }

    async def _preprocess(self, payload: Dict, **kwargs):
        pass

    async def _fetch(self, payload: Dict, **kwargs):
        api_payload = {k: v for k, v in payload.items() if k != "bundle_id"}

        params = self.get_request_param(api_payload)
        timeout = aiohttp.ClientTimeout(total=300)

        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.post(params['url'], headers=params['headers'], json=params['json']) as response:
                rlt = await response.text()
                if response.status >= 400:
                    print(f"Tenderly Bundle API warning (HTTP {response.status}): {rlt}")
                return rlt

    async def _process(self, result_text: str, **kwargs):
        try:
            result = json.loads(result_text)
        except Exception as e:
            print(f"JSON parse error: {e}")
            return None

        simulations = result.get('simulation_results', [])
        if not simulations:
            if "error" in result:
                print(f"simulation error: {result['error']}")
            raise Exception("Bundle simulation error: No simulations returned")

        return simulations
