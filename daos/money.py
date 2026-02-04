import asyncio
from collections import defaultdict
from typing import List, Dict, Union

import networkx as nx
from networkx import MultiDiGraph, DiGraph

from daos.trace import PCTraceItem
from downloaders.defs import Downloader
from downloaders.trans import BlockTimestampDownloader
from utils.price import platform_to_chain, get_token_price
from utils.web3 import hex_to_dec, parse_token_transfer

ZERO_ADDRESS = '0x' + '0' * 40
WETH_ADDRESS = '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'  # WETH


class TransferItem:
    def __init__(
            self, from_address: str, to_address: str,
            amount: int, index: int, contract: str, name: str,
            pc_item: PCTraceItem,
            transaction_hash: str,
            amount_type: str,
            timestamp: int = 0,
            usd_value: float = 0.0
    ):
        self.from_address = from_address
        self.to_address = to_address
        self.amount = amount
        self.index = index
        self.contract = contract
        self.name = name
        self.pc_item = pc_item
        self.transaction_hash = transaction_hash
        self.timestamp = timestamp
        self.usd_value = usd_value
        self.amount_type = amount_type

    def __str__(self):
        return '{}->{} | Asset:{} | Amt:{} ({}) | Value:${:.2f} | Time:{}'.format(
            self.from_address, self.to_address, self.contract,
            self.amount, self.amount_type, self.usd_value, self.timestamp
        )


class MoneyTransferDao:
    def __init__(self, downloader: Downloader, rpc_bucket, tx2pc_list, property_dict):
        self.downloader = downloader
        self.rpc_bucket = rpc_bucket
        self.tx2pc_list = tx2pc_list
        self.property_dict = property_dict

    async def _get_money_transfers(
            self, transaction_hash_list: List[str]
    ) -> List[TransferItem]:

        download_tasks = [self.downloader.download(transaction_hash=tx) for tx in transaction_hash_list]
        tx_results = await asyncio.gather(*download_tasks)

        block_numbers = [res["block_number"] for res in tx_results]
        block2time = await self._load_transaction_time(block_numbers)

        global_index = 0

        transfers = list()
        trace, logs, block_numbers = list(), list(), list()

        for transaction_hash, result in zip(transaction_hash_list, tx_results):
            trace.extend(result['trace'])
            logs.extend(result['logs'])
            block_number = result["block_number"]
            timestamp = block2time.get(block_number, 0)
            pc_list = self.tx2pc_list.get(transaction_hash, [])

            # the first trace item must be in the graph,
            # because it is an external transaction
            if trace:
                transfers.append(TransferItem(
                    from_address=trace[0]['from'],
                    to_address=trace[0]['to'],
                    amount=hex_to_dec(trace[0].get('value')),
                    index=0,
                    contract='0x' + '0' * 40,
                    name='native token',
                    pc_item=PCTraceItem(
                        transaction_hash=transaction_hash, index=-1,
                        pc=-1, opcode='', depth=-1, address='', is_error=False,
                    ),
                    transaction_hash=transaction_hash,
                    timestamp=timestamp,
                    amount_type="raw amount without decimal"
                ))

            # extract transfer from trace and logs
            trace_idx, logs_idx = 1, 0
            is_trace_transfer = {
                op: True for op in [
                    'CALL', 'CALLCODE', 'STATICCALL', 'DELEGATECALL',
                    'CREATE', 'SELFDESTRUCT',
                ]
            }
            is_log = {'LOG%s' % i: True for i in range(4 + 1)}
            for i, pc_item in enumerate(pc_list):
                if is_trace_transfer.get(pc_item.opcode):
                    if trace_idx < len(trace):
                        transfers.append(TransferItem(
                            from_address=trace[trace_idx]['from'],
                            to_address=trace[trace_idx]['to'],
                            amount=hex_to_dec(trace[trace_idx].get('value')),
                            index=global_index,
                            contract=ZERO_ADDRESS,
                            name='native token',
                            pc_item=pc_item,
                            transaction_hash=transaction_hash,
                            timestamp=timestamp,
                            amount_type="raw amount without decimal"
                        ))
                        trace_idx += 1
                        global_index += 1
                    continue

                if not is_log.get(pc_item.opcode) or logs_idx >= len(logs):
                    continue
                token_transfer = parse_token_transfer(logs[logs_idx])
                logs_idx += 1
                if token_transfer is not None:
                    transfers.append(TransferItem(
                        from_address=token_transfer['from'],
                        to_address=token_transfer['to'],
                        amount=hex_to_dec(token_transfer['value']),
                        index=global_index,
                        contract=token_transfer['symbol'],
                        name='ERC token',
                        pc_item=pc_item,
                        transaction_hash=transaction_hash,
                        timestamp=timestamp,
                        amount_type="raw amount without decimal"
                    ))
                    global_index += 1
        return transfers

    async def _calculate_usd_values(self, transfers: List[TransferItem], _platform: str) -> List[TransferItem]:
        chain = platform_to_chain.get(_platform, "")
        if not chain:
            return transfers
        # timestamp -> set(contract_address)
        query_map = defaultdict(set)

        for transfer in transfers:
            contract = WETH_ADDRESS if transfer.contract == ZERO_ADDRESS else transfer.contract
            query_map[transfer.timestamp].add(contract)

        price_data_cache = {}  # Key: "timestamp:contract", Value: info_dict

        async def fetch_price_at_time(ts, contracts):
            coins_query = ",".join([f"{chain}:{addr}" for addr in contracts])
            try:
                if not coins_query:
                    return

                prices = await get_token_price(coins_query, ts)
                for key, info in prices.items():
                    addr = key.split(':')[-1]
                    price_data_cache[f"{ts}:{addr}"] = info
            except Exception as e:
                print(f"Error fetching prices at {ts}: {e}")

        tasks = []
        for ts, contracts in query_map.items():
            tasks.append(fetch_price_at_time(ts, contracts))
        if tasks:
            await asyncio.gather(*tasks)

        for transfer in transfers:
            query_contract = WETH_ADDRESS if transfer.contract == ZERO_ADDRESS else transfer.contract
            cache_key = f"{transfer.timestamp}:{query_contract}"
            token_info = price_data_cache.get(cache_key, {})
            property_info = self.property_dict.get(query_contract, {})

            price = token_info.get('price', 0.0)

            decimals = property_info.get("decimals")
            if decimals is None:
                decimals = token_info.get("decimals")

            name = property_info.get("name", "...")
            symbol = property_info.get("token_symbol")
            if not symbol:
                symbol = token_info.get("symbol", "UNKNOWN")

            if decimals is None:
                if transfer.contract == ZERO_ADDRESS:
                    decimals = 18
                else:
                    decimals = -1

            if decimals is not None and decimals >= 0:
                try:
                    readable_amount = transfer.amount / (10 ** decimals)
                    transfer.amount = readable_amount
                    transfer.amount_type = "processed amount with decimal"
                    if price:
                        transfer.usd_value = readable_amount * price
                    else:
                        transfer.usd_value = 0.0
                except Exception:
                    transfer.usd_value = 0.0
            else:
                transfer.usd_value = 0.0
                transfer.amount_type = "raw amount without decimal"

            if transfer.contract != ZERO_ADDRESS:
                transfer.name = f"{symbol}({name})({transfer.contract})"
        return transfers

    def _process_money_transfers(self, transfers: List[TransferItem]) -> List[TransferItem]:
        # reflect the token transfer pc item to caller
        zero_address = '0x' + '0' * 40
        naive_transfers = list()

        for i, transfer in enumerate(transfers):
            if transfer.contract == zero_address:
                naive_transfers.append(transfer)
                continue
            if transfer.from_address == transfer.pc_item.address:
                continue
            for naive_transfer in reversed(naive_transfers):
                if naive_transfer.transaction_hash != transfer.transaction_hash:
                    break
                if naive_transfer.pc_item.address == transfer.from_address:
                    transfers[i].pc_item = naive_transfer.pc_item
                    break

        # filter zero amount transfer
        rlt = [transfer for transfer in transfers if transfer.amount > 0]
        for i in range(len(rlt)):
            rlt[i].index = i
        return rlt

    async def get_transfer_graph(
            self, transaction_hash_list: List[str],
            pc_list: List[PCTraceItem],
            _platform: str
    ) -> tuple[MultiDiGraph, DiGraph]:
        """
        Get a graph from the external transaction,
        internal transactions, and token transfers.

        :param transaction_hash_list: A list of transaction hash.
        :param pc_list: the pc list while transaction execution.
        :param _platform: DApp platform
        :return: A graph.
        """
        # load transfer data
        transfers = await self._get_money_transfers(
            transaction_hash_list=transaction_hash_list
        )

        transfers = self._process_money_transfers(transfers)

        transfers = await self._calculate_usd_values(transfers, _platform)

        # extract all transfer by account
        addresses = set()
        addr2transfer_out = dict()
        addr2transfer_in = dict()
        for transfer in transfers:
            addresses.add(transfer.from_address)
            addresses.add(transfer.to_address)
            if not addr2transfer_out.get(transfer.from_address):
                addr2transfer_out[transfer.from_address] = list()
            addr2transfer_out[transfer.from_address].append(transfer)
            if not addr2transfer_in.get(transfer.to_address):
                addr2transfer_in[transfer.to_address] = list()
            addr2transfer_in[transfer.to_address].append(transfer)

        # build graph nodes
        # address A -> address B
        address_flow_graph = nx.MultiDiGraph()
        for transfer in transfers:
            address_flow_graph.add_edge(
                transfer.from_address, transfer.to_address,
                amount=transfer.amount,
                usd_value=transfer.usd_value,
                index=transfer.index,
                symbol=transfer.contract,
                name=transfer.name,
                timestamp=transfer.timestamp,
                tx_hash=transfer.transaction_hash,
                amount_type=transfer.amount_type
            )

        # transfer A -> transfer B
        transfer_event_graph = nx.DiGraph()
        for transfer in transfers:
            transfer_event_graph.add_node(transfer.index, info=transfer)

        # build graph edges by strategies
        for transfer in reversed(transfers):
            # token redirection
            if transfer.index - 1 >= 0 and transfers[transfer.index - 1].contract != transfer.contract:
                swap_transfer_idx = transfer.index - 1
                if transfer.from_address == transfers[swap_transfer_idx].to_address:
                    transfer_event_graph.add_edge(
                        transfers[swap_transfer_idx].index,
                        transfer.index,
                        weight=1.0,
                    )
                elif transfer.to_address == transfers[swap_transfer_idx].from_address:
                    transfer_event_graph.add_edge(
                        transfer.index,
                        transfers[swap_transfer_idx].index,
                        weight=1.0,
                    )
                    continue

            # weight pollution and temporal reasoning
            txs_in_linked = [
                tx_in for tx_in in addr2transfer_in.get(transfer.from_address, list())
                if tx_in.contract == transfer.contract and tx_in.index < transfer.index
            ]
            sum_link = sum([_tx.amount for _tx in txs_in_linked])
            for tx_in in txs_in_linked:
                transfer_event_graph.add_edge(
                    tx_in.index, transfer.index,
                    weight=tx_in.amount / sum_link,
                )
        return address_flow_graph, transfer_event_graph

    async def _load_transaction_time(self, block_numbers: List[Union[int, str]]) -> Dict[Union[int, str], int]:
        unique_blocks = list(set(block_numbers))

        async def _create_task(block_number):
            ts = await BlockTimestampDownloader(
                rpc_url=await self.rpc_bucket.get(),
            ).download(block_number=block_number)
            return block_number, ts

        tasks = [_create_task(bn) for bn in unique_blocks]
        results = await asyncio.gather(*tasks)
        return {bn: ts for bn, ts in results}
