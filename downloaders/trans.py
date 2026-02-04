from typing import Union

from downloaders.defs import JSONRPCDownloader, Downloader
from downloaders.trace import FlatTraceDownloader
import asyncio
import json
import os
import urllib.parse
from typing import Dict
from downloaders.defs import EtherscanDownloader
from settings import CACHE_DIR


class TransactionDownloader(Downloader):
    def __init__(self, rpc_url: str):
        self.rpc_url = rpc_url

    async def _preprocess(self, transaction_hash: str, **kwargs):
        return None

    async def _fetch(self, transaction_hash: str, **kwargs):
        return await asyncio.gather(*[
            asyncio.create_task(FlatTraceDownloader(self.rpc_url).download(transaction_hash=transaction_hash)),
            asyncio.create_task(TxReceiptDownloader(self.rpc_url).download(transaction_hash=transaction_hash)),
        ])

    async def _process(self, result, *args, **kwargs):
        trace, receipt = result
        return {
            'trace': trace,
            'logs': receipt["logs"],
            'block_number': receipt["blockNumber"]
        }


class TxReceiptDownloader(JSONRPCDownloader):
    def get_request_param(self, transaction_hash: str) -> Dict:
        data = {
            "id": 1,
            "jsonrpc": "2.0",
            "params": [transaction_hash.lower()],
            "method": "eth_getTransactionReceipt"
        }
        return {
            "url": self.rpc_url,
            "json": data,
        }

    async def _preprocess(self, transaction_hash: str):
        path = os.path.join(CACHE_DIR, 'tx_detail/tx_receipt', '%s.json' % transaction_hash)
        if not os.path.exists(path):
            return None
        with open(path, 'r') as f:
            return json.load(f)

    async def _process(self, result: str, **kwargs):
        result = json.loads(result)
        result = result["result"]

        # cache data
        transaction_hash = kwargs['transaction_hash']
        path = os.path.join(CACHE_DIR, 'tx_detail/tx_receipt')
        if not os.path.exists(path):
            os.makedirs(path)
        path = os.path.join(path, '%s.json' % transaction_hash)
        with open(path, 'w') as f:
            json.dump(result, f)
        return result


class EventLogDownloader(JSONRPCDownloader):
    async def _preprocess(self, transaction_hash: str):
        path = os.path.join(CACHE_DIR, 'tx_detail/tx_receipt', '%s.json' % transaction_hash)
        if not os.path.exists(path):
            return None
        with open(path, 'r') as f:
            receipt = json.load(f)
            if not receipt:
                return None
            return receipt["logs"]

    async def _fetch(self, transaction_hash: str):
        receipt = await TxReceiptDownloader(rpc_url=self.rpc_url).download(transaction_hash=transaction_hash)
        return receipt

    async def _process(self, result: dict, **kwargs):
        return result.get("logs", [])


class TxDownloader(JSONRPCDownloader):
    def get_request_param(self, transaction_hash: str) -> Dict:
        data = {
            "id": 1,
            "jsonrpc": "2.0",
            "params": [transaction_hash.lower()],
            "method": "eth_getTransactionByHash"
        }
        return {
            "url": self.rpc_url,
            "json": data,
        }

    async def _preprocess(self, transaction_hash: str, **kwargs):
        path = os.path.join(CACHE_DIR, 'tx_detail/tx', '%s.json' % transaction_hash)
        if not os.path.exists(path):
            return None
        with open(path, 'r') as f:
            return json.load(f)

    async def _process(self, result: str, **kwargs):
        result_dict = json.loads(result)
        if result_dict is None or result_dict.get("result", None) is None:
            return None

        tx_data = result_dict['result']
        # cache data
        transaction_hash = kwargs['transaction_hash']
        path = os.path.join(CACHE_DIR, 'tx_detail/tx')
        if not os.path.exists(path):
            os.makedirs(path)
        path = os.path.join(path, '%s.json' % transaction_hash)
        with open(path, 'w') as f:
            json.dump(tx_data, f)
        return tx_data


class TxDetailDownloader(JSONRPCDownloader):
    async def _preprocess(self, transaction_hash: str, **kwargs):
        return None

    async def _fetch(self, transaction_hash: str, **kwargs):
        return await asyncio.gather(*[
            asyncio.create_task(TxReceiptDownloader(self.rpc_url).download(transaction_hash=transaction_hash)),
            asyncio.create_task(TxDownloader(self.rpc_url).download(transaction_hash=transaction_hash)),
        ])

    async def _process(self, result, *args, **kwargs):
        tx_receipt, tx = result
        return {
            'tx_receipt': tx_receipt,
            'tx': tx
        }


class BlockTimestampDownloader(JSONRPCDownloader):
    def get_request_param(self, block_number: Union[int, str]) -> Dict:
        if isinstance(block_number, int):
            block_number = hex(block_number)
        data = {
            "id": 1,
            "jsonrpc": "2.0",
            "params": [block_number, False],
            "method": "eth_getBlockByNumber"
        }
        return {
            "url": self.rpc_url,
            "json": data,
        }

    async def _preprocess(self, block_number: Union[int, str]):
        if isinstance(block_number, str):
            bn_int = int(block_number, 16)
        else:
            bn_int = block_number

        path = os.path.join(CACHE_DIR, 'timestamp', f'{bn_int}.json')
        if os.path.exists(path):
            with open(path, 'r') as f:
                return json.load(f)
        return None

    async def _process(self, result: str, **kwargs):
        res_json = json.loads(result)
        timestamp_hex = res_json["result"]["timestamp"]
        timestamp = int(timestamp_hex, 16)

        block_number = kwargs['block_number']
        if isinstance(block_number, str):
            block_number = int(block_number, 16)

        path = os.path.join(CACHE_DIR, 'timestamp')
        if not os.path.exists(path):
            os.makedirs(path)

        save_path = os.path.join(path, f'{block_number}.json')
        with open(save_path, 'w') as f:
            json.dump(timestamp, f)

        return timestamp


async def test():
    d = TransactionDownloader('https://eth-mainnet.nodereal.io/v1/317f6d43dd4c4acea1fa00515cf02f90')
    rlt = await d.download(transaction_hash='0x54e45ce9b037a6e353284533958147a607ff0569670d62add99d5f5f3b9e09e9')
    print(rlt)


if __name__ == '__main__':
    asyncio.run(test())
