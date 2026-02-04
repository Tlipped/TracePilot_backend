import asyncio
import os
from collections import defaultdict
from typing import List, Dict

from agents.AgentBase import AgentBase
from daos.trace import TraceDao
from downloaders.trace import RawTraceDownloader
from downloaders.trans import TxReceiptDownloader
from prompt.tx_role_analyze_prompt import TX_ROLE_ANALYZE_SP, TX_ROLE_ANALYZE_UP
from settings import CACHE_DIR
from utils.balance_utils import NATIVE_TOKEN_ADDRESS, BalanceAnalyzer
from utils.bucket import AsyncItemBucket
from utils.price import get_token_price, platform_to_chain
from utils.signature import hex_to_int
from utils.token_utils import ERC20_TRANSFER_TOPIC, parse_address


class TxRoleAgent(AgentBase):
    def __init__(self, apikey_bucket: AsyncItemBucket, rpc_bucket: AsyncItemBucket, _platform, dapp_name,
                 name="TxRoleAgent"):
        super(TxRoleAgent, self).__init__(name, TX_ROLE_ANALYZE_SP, unique_id=dapp_name)
        self.apikey_bucket = apikey_bucket
        self.rpc_bucket = rpc_bucket
        self._platform = _platform
        self.erc20_transfer_topic = ERC20_TRANSFER_TOPIC
        self.NATIVE_TOKEN_ADDRESS = NATIVE_TOKEN_ADDRESS
        self.WETH_ADDRESS = '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'

    async def handle(self, dapp_data: Dict, transactions: List[Dict]) -> tuple[Dict, List[str], List[str], Dict]:
        roles = {}
        attack_transactions = []
        auxiliary_transactions = []

        balance_change = await self.get_balance_change(dapp_data, transactions)

        # transaction role analyze
        role_path = os.path.join(CACHE_DIR, 'summary/role_summary', '%s.json' % dapp_data.get("dapp").get("name"))
        if os.path.exists(role_path):
            role_summary = self.load_summary_from_cache(str(role_path))
        else:
            role_summary = await self.query(TX_ROLE_ANALYZE_UP.format(
                tx_hash=dapp_data.get("transaction_hash_list", ""),
                tx_detail=dapp_data.get("transaction_detail", ""),
                transfer_graph=dapp_data.get("transfer_graph", ""),
                tx_token_property=self.process_property(dapp_data),
                balance_change=balance_change
            ), _format="json")
            # write cache
            self.write_summary_to_cache(cache_path='summary/role_summary', file_name=dapp_data.get("dapp").get("name"),
                                        summary=role_summary)

        if role_summary:
            if "transaction" in role_summary and isinstance(role_summary["transaction"], dict):
                attack_transactions = role_summary["transaction"].get("attack_transactions", [])
                auxiliary_transactions = role_summary["transaction"].get("auxiliary_transactions", [])
            if "address_role" in role_summary and isinstance(role_summary["address_role"], dict):
                roles = role_summary["address_role"]

        return roles, attack_transactions, auxiliary_transactions, balance_change

    def process_property(self, dapp_data):
        tx2property = dapp_data.get("transaction_to_property")
        return " ".join(
            str(_tx) + ":" + " ".join(str(item) for item in _property) for _tx, _property in tx2property.items())

    async def get_balance_change(self, dapp_data: Dict, transactions: List[Dict]):
        tx_hashes = dapp_data.get("transaction_hash_list", [])
        tx2property = dapp_data.get("transaction_to_property", {})
        tenderly_macro_map = dapp_data.get("transaction_to_detail", {}) or {} if dapp_data else {}

        platform = dapp_data.get("dapp", {}).get("platform", "Ethereum")

        hash_to_ts = {tx.get("tx_hash"): tx.get("timestamp") for tx in transactions}

        price_map = await self._fetch_prices_for_all_txs(tx_hashes, hash_to_ts, tenderly_macro_map, tx2property,
                                                         platform)

        missing = [tx for tx in tx_hashes if tx not in tenderly_macro_map]
        traces_map, receipts_map = {}, {}
        if missing:
            traces_map, receipts_map = await asyncio.gather(
                self._load_trace_tree(missing),
                self._load_tx_receipts(missing)
            )

        results = {}
        global_metadata = self._build_initial_metadata(tx2property)

        for tx_hash in tx_hashes:
            macro_for_tx = tenderly_macro_map.get(tx_hash)

            raw_balance_changes = defaultdict(lambda: defaultdict(int))

            if macro_for_tx:
                changes, extracted_meta = BalanceAnalyzer.parse_tenderly_data(macro_for_tx)
                raw_balance_changes = changes
                global_metadata.update(extracted_meta)
            else:
                trace = traces_map.get(tx_hash)
                receipt = receipts_map.get(tx_hash)
                if receipt:
                    self._calculate_gas_cost(receipt, raw_balance_changes)
                    self._analyze_token_changes(receipt.get('logs', []), raw_balance_changes)
                if trace:
                    self._analyze_eth_changes(trace, raw_balance_changes)

            tx_price_info = price_map.get(tx_hash, {})
            results[tx_hash] = BalanceAnalyzer.calculate_usd_values(raw_balance_changes, global_metadata, tx_price_info, self._platform)

        return results

    async def _fetch_prices_for_all_txs(self, tx_hashes, hash_to_ts, tenderly_map, tx2prop, platform) -> Dict:
        chain = platform_to_chain.get(platform, "ethereum")
        price_results = {}

        for tx_hash in tx_hashes:
            ts = hash_to_ts.get(tx_hash)
            if not ts: continue

            query_addrs = {self.NATIVE_TOKEN_ADDRESS.lower()}

            props = tx2prop.get(tx_hash, [])
            for p in props:
                a = p.get('contract_address')
                if a: query_addrs.add(a.lower())

            macro = tenderly_map.get(tx_hash)
            if macro:
                for asset in macro.get('asset_changes', []):
                    a = asset.get('token_info', {}).get('contract_address')
                    if a: query_addrs.add(a.lower())

            final_query_list = []
            for addr in query_addrs:
                target = self.WETH_ADDRESS if addr == self.NATIVE_TOKEN_ADDRESS else addr
                final_query_list.append(f"{chain}:{target}")

            final_query_str = ",".join(set(final_query_list))

            try:
                prices = await get_token_price(final_query_str, ts)

                tx_prices = {}
                for key, info in prices.items():
                    token_addr = key.split(':')[-1].lower()
                    price = info.get('price', 0.0)
                    tx_prices[token_addr] = price
                    if token_addr == self.WETH_ADDRESS:
                        tx_prices[self.NATIVE_TOKEN_ADDRESS] = price

                price_results[tx_hash] = tx_prices
            except Exception as e:
                print(f"Price fetch error for {tx_hash}: {e}")

        return price_results

    def _build_initial_metadata(self, tx2property: Dict) -> Dict[str, Dict]:
        meta = {}
        for _tx, props in tx2property.items():
            for p in props:
                addr = p.get('contract_address', '').lower()
                if addr:
                    meta[addr] = {
                        "symbol": p.get('token_symbol') or p.get('symbol', 'UNKNOWN'),
                        "name": p.get('name', 'Unknown Token'),
                        "decimals": p.get('decimals', 18),
                        "contract": addr
                    }
        return meta

    def _analyze_eth_changes(self, trace_node: Dict, changes: Dict[str, Dict[str, int]]):
        if not trace_node:
            return
        value_hex = trace_node.get('value', '0x0')
        value = hex_to_int(value_hex)
        from_addr = trace_node.get('from', '').lower()
        to_addr = trace_node.get('to', '').lower()

        if value > 0 and from_addr and to_addr:
            changes[from_addr][self.NATIVE_TOKEN_ADDRESS] -= value
            changes[to_addr][self.NATIVE_TOKEN_ADDRESS] += value

        for sub_call in trace_node.get('calls', []):
            self._analyze_eth_changes(sub_call, changes)

    def _analyze_token_changes(self, logs: List[Dict], changes: Dict[str, Dict[str, int]]):
        for log in logs:
            topics = log.get('topics', [])
            if len(topics) == 3 and topics[0].lower() == self.erc20_transfer_topic:
                token_addr = log.get('address', '').lower()
                from_addr = parse_address(topics[1]).lower()
                to_addr = parse_address(topics[2]).lower()
                data_hex = log.get('data', '0x0')
                amount = hex_to_int(data_hex)

                if amount > 0:
                    changes[from_addr][token_addr] -= amount
                    changes[to_addr][token_addr] += amount

    def _calculate_gas_cost(self, receipt: Dict, changes: Dict[str, Dict[str, int]]):
        if not receipt:
            return
        gas_used = hex_to_int(receipt.get('gasUsed', '0x0'))
        gas_price = hex_to_int(receipt.get('effectiveGasPrice', receipt.get('gasPrice', '0x0')))
        total_fee = gas_used * gas_price

        sender = receipt.get('from', '').lower()

        if sender and total_fee > 0:
            changes[sender][self.NATIVE_TOKEN_ADDRESS] -= total_fee

    async def _load_trace_tree(self, transaction_hashs: List[str]) -> Dict[str, Dict]:
        async def _create_task(_transaction_hash: str):
            return await TraceDao(downloader=RawTraceDownloader(
                rpc_url=await self.rpc_bucket.get(),
            )).get_call_tree(transaction_hash=_transaction_hash)

        tasks = [_create_task(txhash) for txhash in transaction_hashs]
        result = await asyncio.gather(*tasks)
        return {txhash: result[i] for i, txhash in enumerate(transaction_hashs)}

    async def _load_tx_receipts(self, transaction_hashs: List[str]) -> Dict[str, List]:
        async def _create_task(_transaction_hash: str):
            return await TxReceiptDownloader(
                rpc_url=await self.rpc_bucket.get(),
            ).download(transaction_hash=_transaction_hash)

        tasks = [_create_task(txhash) for txhash in transaction_hashs]
        result = await asyncio.gather(*tasks)
        return {txhash: result[i] for i, txhash in enumerate(transaction_hashs)}
