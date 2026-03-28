import asyncio
import json
import os.path
from typing import List, Dict

from tqdm import tqdm

from agents.AgentBase import AgentBase
from daos.property import TokenPropertyDao, TokenPropertyItem
from daos.tenderly import TenderlyDao
from daos.tx import TxDetailDao
from downloaders.contract import ContractSourceDownloader, ContractBytecodeDownloader, ContractCreationDownloader
from downloaders.property import TokenPropertyDownloader
from downloaders.trans import EventLogDownloader, TxDownloader
from prompt.tx_summary_prompt import TX_SUMMARY_SP, TX_SUMMARY_UP
from settings import CACHE_DIR
from utils.bucket import AsyncItemBucket


class TxDetailAgent(AgentBase):
    def __init__(self, apikey_bucket: AsyncItemBucket, rpc_bucket: AsyncItemBucket, _platform, dapp_name,
                 name="TxDetailAgent", log_callback=None):
        super(TxDetailAgent, self).__init__(name, TX_SUMMARY_SP, unique_id=dapp_name,log_callback=log_callback)
        self.apikey_bucket = apikey_bucket
        self.rpc_bucket = rpc_bucket
        self._platform = _platform

    async def handle(self, tx_list: List[str]) -> tuple[str, Dict[str, Dict], dict[str, list[TokenPropertyItem]]]:
        tx_detail_str = ''

        # get token info
        txhash2property = await self._load_token_properties(tx_list)
        addr2property, addr2proname, property_str = self.process_properties(txhash2property)

        txhash2detail = await self._load_tx_detail_data(tx_list, addr2property, addr2proname)

        # transaction summary
        for tx in tqdm(tx_list, total=len(tx_list), desc="Detail Summary"):
            # check cache
            detail_path = os.path.join(CACHE_DIR, 'summary/tx_detail_summary', '%s.json' % tx)
            if os.path.exists(detail_path):
                tx_summary = self.load_summary_from_cache(str(detail_path))
            else:
                tx_detail = txhash2detail.get(tx, {})
                detail_copy = tx_detail.copy()
                detail_copy.pop('state_diff', None)
                detail_copy.pop('balance_diff', None)
                detail_copy.pop('balance_changes', None)
                detail_copy.pop('asset_changes', None)
                tx_json_str = json.dumps(detail_copy, indent=4)
                tx_summary = await self.query(TX_SUMMARY_UP.format(
                    tx_detail=tx_json_str,
                    properties=property_str
                ), _format="str")
                # write cache
                self.write_summary_to_cache(cache_path='summary/tx_detail_summary', file_name=tx,
                                            summary=tx_summary)
            tx_detail_str = tx_detail_str + tx + ': ' + tx_summary + '\n'
        return tx_detail_str, txhash2detail, txhash2property

    async def _load_tx_detail_data(self, transaction_hashs: List[str], addr2property: Dict, addr2proname: Dict) -> Dict[str, Dict]:
        async def _create_task(_transaction_hash: str):
            return await TxDetailDao(tenderly_dao=TenderlyDao(
                _platform=self._platform,
                tx_downloader=TxDownloader(rpc_url=await self.rpc_bucket.get()),
                source_downloader=ContractSourceDownloader(apikey=await self.apikey_bucket.get()),
                bytecode_downloader=ContractBytecodeDownloader(rpc_url=await self.rpc_bucket.get()),
                creation_downloader=ContractCreationDownloader(apikey=await self.apikey_bucket.get()),
                addr2property=addr2proname
            ), properties=addr2property).get_tx_detail(transaction_hash=_transaction_hash)

        tasks = [_create_task(txhash) for txhash in transaction_hashs]
        result = await asyncio.gather(*tasks)
        txhash2detail = dict()
        for i, txhash in enumerate(transaction_hashs):
            txhash2detail[txhash] = result[i]
        return txhash2detail

    async def _load_token_properties(self, transaction_hashs: List[str]) -> Dict[str, List[TokenPropertyItem]]:
        async def _create_task(_transaction_hash: str):
            return await TokenPropertyDao(property_downloader=TokenPropertyDownloader(
                rpc_url=await self.rpc_bucket.get()
            ), event_log_downloader=EventLogDownloader(
                rpc_url=await self.rpc_bucket.get())
            ).get_token_property_from_transaction(transaction_hash=_transaction_hash)

        tasks = [_create_task(txhash) for txhash in transaction_hashs]
        results = await asyncio.gather(*tasks)
        txhash2property = dict()
        for i, txhash in enumerate(transaction_hashs):
            txhash2property[txhash] = results[i]
        return txhash2property

    def process_properties(self, txhash2property):
        addr2property = {}
        addr2proname = {}
        for tx, properties in txhash2property.items():
            for _property in properties:
                single = _property.to_dict()
                address = single.get("contract_address")
                _dict = _property.to_dict()
                addr2property[address] = _dict
                addr2proname[address] = _dict.get("name")

        property_str = " ".join(
            str(_tx) + ":" + " ".join(str(item) for item in _property) for _tx, _property in txhash2property.items())
        return addr2property, addr2proname, property_str
