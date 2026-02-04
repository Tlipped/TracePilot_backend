import json
import os
import urllib.parse
from typing import Dict

from downloaders.defs import JSONRPCDownloader, EtherscanDownloader
from settings import CACHE_DIR


class ContractBytecodeDownloader(JSONRPCDownloader):
    def get_request_param(self, contract_address: str, quantity: str = 'latest') -> Dict:
        data = {
            "id": 1,
            "jsonrpc": "2.0",
            "params": [
                contract_address.lower(),
                quantity,
            ],
            "method": "eth_getCode"
        }
        return {
            "url": self.rpc_url,
            "json": data,
        }

    async def _preprocess(self, contract_address: str, **kwargs):
        path = os.path.join(CACHE_DIR, 'bytecode', '%s.json' % contract_address)
        if not os.path.exists(path):
            return None
        with open(path, 'r') as f:
            return json.load(f)

    async def _process(self, result: str, **kwargs):
        result = json.loads(result)
        result = result['result']

        # cache data
        contract_address = kwargs['contract_address']
        path = os.path.join(CACHE_DIR, 'bytecode')
        if not os.path.exists(path):
            os.makedirs(path)
        path = os.path.join(path, '%s.json' % contract_address)
        with open(path, 'w') as f:
            json.dump(result, f)
        return result


class ContractSourceDownloader(EtherscanDownloader):
    def get_request_param(self, contract_address: str) -> Dict:
        query_params = urllib.parse.urlencode({
            "module": "contract",
            "action": "getsourcecode",
            "address": contract_address.lower(),
        })
        return {"url": '{}&{}'.format(self.apikey, query_params)}

    async def _preprocess(self, contract_address: str, **kwargs):
        path = os.path.join(CACHE_DIR, 'source', '%s.json' % contract_address)
        if not os.path.exists(path):
            return None
        with open(path, 'r') as f:
            return json.load(f)

    async def _process(self, result: str, **kwargs):
        result = json.loads(result)
        result = result['result'][0]

        # cache data
        contract_address = kwargs['contract_address']
        path = os.path.join(CACHE_DIR, 'source')
        if not os.path.exists(path):
            os.makedirs(path)
        path = os.path.join(path, '%s.json' % contract_address)
        with open(path, 'w') as f:
            json.dump(result, f)
        return result


class ContractABIDownloader(EtherscanDownloader):
    def get_request_param(self, contract_address: str) -> Dict:
        query_params = urllib.parse.urlencode({
            "module": "contract",
            "action": "getabi",
            "address": contract_address.lower(),
        })
        return {"url": '{}&{}'.format(self.apikey, query_params)}

    async def _preprocess(self, contract_address: str, **kwargs):
        path = os.path.join(CACHE_DIR, 'abi', '%s.json' % contract_address)
        if not os.path.exists(path):
            return None
        with open(path, 'r') as f:
            return json.load(f)

    async def _process(self, result: str, **kwargs):
        result = json.loads(result)
        if result.get("status", '') != '1':
            return None
        result = json.loads(result['result'])
        # cache data
        contract_address = kwargs['contract_address']
        path = os.path.join(CACHE_DIR, 'abi')
        if not os.path.exists(path):
            os.makedirs(path)
        path = os.path.join(path, '%s.json' % contract_address)
        with open(path, 'w') as f:
            json.dump(result, f)
        return result


class ContractCreationDownloader(EtherscanDownloader):
    def get_request_param(self, contract_address: str) -> Dict:
        query_params = urllib.parse.urlencode({
            "module": "contract",
            "action": "getcontractcreation",
            "contractaddresses": contract_address.lower(),
        })
        return {"url": '{}&{}'.format(self.apikey, query_params)}

    async def _preprocess(self, contract_address: str, **kwargs):
        path = os.path.join(CACHE_DIR, 'creation', '%s.json' % contract_address)
        if not os.path.exists(path):
            return None
        with open(path, 'r') as f:
            return json.load(f)

    async def _process(self, result: str, **kwargs):
        result_json = json.loads(result)

        if result_json.get("status") != "1" or not result_json.get("result"):
            print(f"[ContractCreationDownloader] Failed or no result for {kwargs.get('contract_address')}")
            return None

        info = result_json["result"][0]
        parsed_data = {
            "creator": info.get("contractCreator"),
            "tx_hash": info.get("txHash")
        }

        # cache data
        contract_address = kwargs['contract_address']
        path = os.path.join(CACHE_DIR, 'creation')
        if not os.path.exists(path):
            os.makedirs(path)
        path = os.path.join(path, '%s.json' % contract_address)
        with open(path, 'w') as f:
            json.dump(parsed_data, f)

        return parsed_data
