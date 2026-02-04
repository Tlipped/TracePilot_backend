import json
import os
from typing import Dict, Optional, Any

import aiohttp
from web3 import Web3

from downloaders.defs import JSONRPCDownloader
from settings import CACHE_DIR
from utils.web3 import parse_bytes_data


class TokenPropertyDownloader(JSONRPCDownloader):
    def __init__(self, rpc_url: str):
        super().__init__(rpc_url)
        self.rpc_url = rpc_url
        self.cache_dir = os.path.join(CACHE_DIR, 'property')

    def get_request_params(self, contract_address: str) -> dict[
        str, list[dict[str, str | list[str] | int | list[dict[str, str | Any] | str]]] | str]:
        requests = []
        property_queries = [
            {'id': 1, 'property_key': 'name', 'func': 'name()', 'return_type': ['string']},
            {'id': 2, 'property_key': 'token_symbol', 'func': 'symbol()', 'return_type': ['string']},
            {'id': 3, 'property_key': 'token_symbol', 'func': 'symbol()', 'return_type': ['bytes32']},
            {'id': 4, 'property_key': 'token_symbol', 'func': 'SYMBOL()', 'return_type': ['string']},
            {'id': 5, 'property_key': 'decimals', 'func': 'decimals()', 'return_type': ['uint8']},
            {'id': 6, 'property_key': 'total_supply', 'func': 'totalSupply()', 'return_type': ['uint256']},
        ]
        for query in property_queries:
            requests.append({
                "jsonrpc": "2.0",
                "method": "eth_call",
                "params": [{
                    'to': contract_address,
                    "data": '0x' + Web3.keccak(text=query['func']).hex()[:2 + 8]
                }, 'latest'],
                "id": query['id']
            })

        return {
            "url": self.rpc_url,
            "json": requests,
        }

    async def _preprocess(self, contract_address: str) -> Optional[Dict]:
        if not self.cache_dir:
            return None

        path = os.path.join(self.cache_dir, f'{contract_address}.json')
        if not os.path.exists(path):
            return None

        try:
            with open(path, 'r') as f:
                return json.load(f)
        except:
            return None

    async def _fetch(self, contract_address: str) -> str:
        params = self.get_request_params(contract_address)
        client = aiohttp.ClientSession()
        try:
            async with client.post(**params) as response:
                return await response.text()
        finally:
            await client.close()

    async def _process(self, result: str, **kwargs) -> Dict:
        try:
            responses = json.loads(result)
            property_data = {
                'name': '',
                'token_symbol': '',
                'decimals': -1,
                'total_supply': -1
            }

            for response in responses:
                if 'error' in response:
                    continue

                data = response.get('result')
                request_id = response.get('id')

                if request_id == 1:  # name (string)
                    parsed = parse_bytes_data(data, ['string'])
                    if parsed and parsed[0]:
                        property_data['name'] = parsed[0].replace('\0', '')

                elif request_id == 2:  # symbol (string)
                    parsed = parse_bytes_data(data, ['string'])
                    if parsed and parsed[0]:
                        property_data['token_symbol'] = parsed[0].replace('\0', '')

                elif request_id == 3:  # symbol (bytes32)
                    if not property_data['token_symbol']:
                        parsed = parse_bytes_data(data, ['bytes32'])
                        if parsed and parsed[0]:
                            property_data['token_symbol'] = parsed[0].decode().replace('\0', '')

                elif request_id == 4:  # SYMBOL (string)
                    if not property_data['token_symbol']:
                        parsed = parse_bytes_data(data, ['string'])
                        if parsed and parsed[0]:
                            property_data['token_symbol'] = parsed[0].replace('\0', '')

                elif request_id == 5:  # decimals
                    parsed = parse_bytes_data(data, ['uint8'])
                    if parsed and parsed[0] is not None:
                        property_data['decimals'] = parsed[0]

                elif request_id == 6:  # totalSupply
                    parsed = parse_bytes_data(data, ['uint256'])
                    if parsed and parsed[0] is not None:
                        property_data['total_supply'] = parsed[0]

            if self.cache_dir:
                os.makedirs(self.cache_dir, exist_ok=True)
                path = os.path.join(self.cache_dir, f'{kwargs["contract_address"]}.json')
                with open(path, 'w') as f:
                    json.dump(property_data, f)

            return property_data

        except Exception as e:
            return {
                'name': '',
                'token_symbol': '',
                'decimals': -1,
                'total_supply': -1
            }
