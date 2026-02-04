import asyncio
from typing import Dict, Any, List

from web3 import Web3

from daos.tenderly import TenderlyDao
from downloaders.defs import Downloader
from entities.tx import TxDetailItem
from utils.log import LogAnalyzer


class TxDetailDao:
    def __init__(self, tenderly_dao: TenderlyDao, properties: Dict):
        self.tenderly_dao = tenderly_dao
        self.log_analyzer = LogAnalyzer(properties)

    async def get_tx_detail(self, transaction_hash: str):
        result = await self.tenderly_dao.simulate_tx_in_tenderly(transaction_hash)

        if not result:
            raise Exception(f"Tenderly returned None for tx {transaction_hash}")
        transaction_data, contracts, addr2name = result

        if not transaction_data:
            raise Exception("Transaction data is empty in Tenderly response")

        tx_entity = TxDetailItem(
            raw_data={"transaction": transaction_data},
            log_analyzer=self.log_analyzer
        )
        return tx_entity.to_dict()
