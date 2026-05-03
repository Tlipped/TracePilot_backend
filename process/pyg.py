import asyncio
import time
from typing import Callable, List, Dict, Optional, Union

from agents.FilterAgent import FilterAgent
from agents.TraceAgent import TraceAgent
from agents.TxDetailAgent import TxDetailAgent
from agents.TxFaultAgent import TxFaultAgent
from agents.TxRoleAgent import TxRoleAgent
from downloaders.trans import BlockTimestampDownloader
from process.fund_graph import FundFlowGraphBuilder
from settings import TRANSACTION_FILTER_SMALL_SET_THRESHOLD
from utils.bucket import AsyncItemBucket
from utils.graph2text import convert_to_graph


class DAppProcess:
    def __init__(
            self,
            net2rpc_bucket: Dict[str, AsyncItemBucket],
            net2apikey_bucket: Dict[str, AsyncItemBucket],
            mcp_client,
            log_callback=None,
            cancellation_checker: Optional[Callable[[], bool]] = None
    ):
        self._net2rpc_bucket = net2rpc_bucket
        self._net2apikey_bucket = net2apikey_bucket
        self.mcp_client = mcp_client
        self.log_callback = log_callback
        self.cancellation_checker = cancellation_checker or (lambda: False)

    def _check_cancelled(self):
        if self.cancellation_checker():
            raise asyncio.CancelledError("Task was cancelled")

    def _attach_cancel(self, agent):
        if hasattr(agent, "set_cancellation_checker"):
            agent.set_cancellation_checker(self.cancellation_checker)
        return agent

    async def process(self, dapp):
        self._check_cancelled()
        processed_data = {'dapp': dapp}
        dapp_name = dapp["name"]

        raw_tx_list = dapp['transaction_hash']

        start_time = time.time()
        # analyze transaction detail
        print("downloading transaction details ~ ")
        self._check_cancelled()
        tx_detail_agent = TxDetailAgent(apikey_bucket=self._net2apikey_bucket[dapp['platform']],
                                        rpc_bucket=self._net2rpc_bucket[dapp['platform']],
                                        _platform=dapp["platform"],
                                        dapp_name=dapp_name,
                                        log_callback=self.log_callback)
        self._attach_cancel(tx_detail_agent)
        tx_detail_str, tx2detail, tx2property = await tx_detail_agent.handle(raw_tx_list)
        self._check_cancelled()
        _tx2property, property_dict = self.process_property(tx2property=tx2property)

        processed_data["transaction_detail"] = tx_detail_str
        processed_data["transaction_to_detail"] = tx2detail
        processed_data["transaction_to_property"] = _tx2property

        # load state diff
        tx2balance_diff, tx2asset_changes = self.load_fund_data(tx2detail)

        # load transfer graph
        print("loading transfer graph ~")
        self._check_cancelled()
        transactions, sorted_tx_list = await self.format_transactions(dapp["platform"], tx2detail, tx2balance_diff,
                                                                      tx2asset_changes)
        processed_data["transaction_hash_list"] = sorted_tx_list
        print(f"transaction_hash_list: {sorted_tx_list}")

        builder = FundFlowGraphBuilder(platform=dapp["platform"])
        self._check_cancelled()
        transfer_graph = await builder.add_transactions(transactions)
        flow_graph_str = str(convert_to_graph(transfer_graph, graph_name="DApp_Transfer_Graph"))
        processed_data["transfer_graph"] = flow_graph_str

        # init trace simulator
        print("loading init trace ~")
        self._check_cancelled()
        trace_agent = TraceAgent(processed_data, mcp_client=self.mcp_client, dapp_name=dapp_name, log_callback=self.log_callback)
        self._attach_cancel(trace_agent)
        tx2init_trace = await trace_agent.init()
        self._check_cancelled()
        processed_data["trace_tree"] = tx2init_trace

        # identify transaction roles
        print("analyzing transaction roles ~ ")
        self._check_cancelled()
        tx_role_agent = TxRoleAgent(apikey_bucket=self._net2apikey_bucket[dapp['platform']],
                                    rpc_bucket=self._net2rpc_bucket[dapp['platform']],
                                    _platform=dapp["platform"],
                                    dapp_name=dapp_name,
                                    log_callback=self.log_callback)
        self._attach_cancel(tx_role_agent)
        roles, attack_transactions, auxiliary_transactions, balance_change = await tx_role_agent.handle(processed_data,
                                                                                                        transactions)
        self._check_cancelled()
        processed_data["transaction_roles"] = roles
        processed_data["balance_change"] = balance_change
        processed_data["attack_transactions"] = attack_transactions
        processed_data["auxiliary_transactions"] = auxiliary_transactions

        # filter attack transactions
        print("filter same attack templates ~ ")
        self._check_cancelled()
        attack_filter = FilterAgent(dapp_name=dapp_name, log_callback=self.log_callback)
        self._attach_cancel(attack_filter)

        num_attack_txs = len(attack_transactions)
        num_total_txs = len(sorted_tx_list)
        if (num_attack_txs == 0 and len(sorted_tx_list) > 0) or (num_total_txs <= TRANSACTION_FILTER_SMALL_SET_THRESHOLD):
            txs_need_analyze = sorted_tx_list
            attack_transactions = sorted_tx_list
            auxiliary_transactions = []
            processed_data["attack_transactions"] = attack_transactions
            processed_data["auxiliary_transactions"] = auxiliary_transactions
        else:
            if num_attack_txs > 1:
                txs_need_analyze = await attack_filter.handle(processed_data)
                self._check_cancelled()
            else:
                txs_need_analyze = attack_transactions

        processed_data["transactions_need_analyze"] = txs_need_analyze

        # macro transaction fault
        print("analyzing transaction bug ~ ")
        self._check_cancelled()
        tx_fault_agent = TxFaultAgent(dapp_name=dapp_name,  log_callback=self.log_callback)
        self._attach_cancel(tx_fault_agent)
        bug_summary = await tx_fault_agent.handle(processed_data)
        self._check_cancelled()
        processed_data["bug_summary"] = bug_summary

        # single = SingleAgent(dapp_name=dapp_name)
        # fault_report = await single.handle(processed_data)

        elapsed_time = time.time() - start_time

        processed_data["token_used"] = {
            "total_token": tx_detail_agent.token + tx_fault_agent.token + tx_role_agent.token + attack_filter.token,
            "detail": tx_detail_agent.token,
            "role": tx_role_agent.token,
            "filter": attack_filter.token,
            "fault": tx_fault_agent.token
        }

        processed_data["time_used"] = {
            "total_time": elapsed_time,
            "detail": tx_detail_agent.get_total_time(),
            "role": tx_role_agent.get_total_time(),
            "filter": attack_filter.get_total_time(),
            "fault": tx_fault_agent.get_total_time()
        }
        return processed_data, trace_agent

    def process_property(self, tx2property):
        _tx2property = {}
        for tx, property_list in tx2property.items():
            _tx2property[tx] = [property_item.to_dict() for property_item in property_list]

        property_dict = {}
        for _tx, _property_list in _tx2property.items():
            for _property in _property_list:
                address = _property.get("contract_address", "")
                if address == "":
                    continue
                property_dict[address] = _property
        return _tx2property, property_dict

    def load_fund_data(self, tx2detail: Dict[str, Dict]):
        tx2balance_diff = self.fully_load(tx2detail, "balance_diff")
        tx2asset_changes = self.fully_load(tx2detail, "asset_changes")
        return tx2balance_diff, tx2asset_changes

    def fully_load(self, tx2detail: Dict[str, Dict], name: str):
        return {tx: detail.get(name, []) for tx, detail in tx2detail.items()}

    async def format_transactions(self, platform, tx2detail, tx2balance_diff, tx2asset_changes):
        transactions = []
        block_numbers = [detail["block_number"] for tx, detail in tx2detail.items()]
        block2time = await self._load_transaction_time(block_numbers, platform)

        for tx, detail in tx2detail.items():
            index = detail["transaction_index"]
            block_number = detail["block_number"]
            timestamp = block2time.get(block_number)
            if not timestamp:
                raise ValueError(f"CRITICAL: Failed to fetch timestamp for block {block_number}. Cannot proceed.")
            balance_diff = tx2balance_diff.get(tx, [])
            asset_changes = tx2asset_changes.get(tx, [])
            transactions.append({
                "tx_hash": tx,
                "timestamp": timestamp,
                "block_number": block_number,
                "index": index,
                "balance_diff": balance_diff,
                "asset_changes": asset_changes
            })
        transactions.sort(key=lambda x: (x['block_number'], x['index']))
        sorted_tx_hashes = [tx['tx_hash'] for tx in transactions]
        return transactions, sorted_tx_hashes

    async def _load_transaction_time(self, block_numbers: List[Union[int, str]], platform: str) -> Dict[Union[int, str], int]:
        unique_blocks = list(set(block_numbers))

        async def _create_task(block_number):
            ts = await BlockTimestampDownloader(
                rpc_url=await self._net2rpc_bucket[platform].get(),
            ).download(block_number=block_number)
            return block_number, ts

        tasks = [_create_task(bn) for bn in unique_blocks]
        results = await asyncio.gather(*tasks)
        return {bn: ts for bn, ts in results}
