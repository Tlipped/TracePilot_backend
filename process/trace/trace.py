import asyncio
from typing import List, Optional, Dict

from daos.property import TokenPropertyDao, TokenPropertyItem
from downloaders.contract import ContractSourceDownloader, ContractBytecodeDownloader, ContractCreationDownloader
from downloaders.property import TokenPropertyDownloader
from downloaders.trans import TxDownloader, EventLogDownloader
from entities.trace import MixTraceItem, TraceNode
from daos.tenderly import MixTraceTree, TenderlyDao
from process.trace.debug_simulator import DebugSimulator
from settings import PLATFORM_TO_CHAIN_ID
from utils.bucket import AsyncItemBucket


class TraceLoader:
    def __init__(self, tx_list: [], apikey_bucket: AsyncItemBucket, rpc_bucket: AsyncItemBucket, _platform):
        self.apikey_bucket = apikey_bucket
        self.rpc_bucket = rpc_bucket
        self.tx_list = tx_list
        self.addr2property = {}
        self._platform = _platform
        self.chain_id = PLATFORM_TO_CHAIN_ID.get(_platform, 1)

    async def load_properties(self):
        tx2property = await self._load_token_properties(self.tx_list)
        for tx, property_list in tx2property.items():
            properties = [property_item.to_dict() for property_item in property_list]
            for _property in properties:
                self.addr2property[_property['contract_address']] = _property['name']

    async def load_micro_data(self):
        tx2micro = await self._load_micro_data(self.tx_list)
        return tx2micro

    async def _load_micro_data(self, transaction_hashs: List[str]):
        async def _create_task(_transaction_hash: str):
            return await TenderlyDao(
                _platform=self._platform,
                tx_downloader=TxDownloader(rpc_url=await self.rpc_bucket.get()),
                source_downloader=ContractSourceDownloader(apikey=await self.apikey_bucket.get()),
                bytecode_downloader=ContractBytecodeDownloader(rpc_url=await self.rpc_bucket.get()),
                creation_downloader=ContractCreationDownloader(apikey=await self.apikey_bucket.get()),
                addr2property=self.addr2property
            ).get_micro_data(_transaction_hash)

        tasks = [_create_task(txhash) for txhash in transaction_hashs]
        result = await asyncio.gather(*tasks)
        return {txhash: result[i] for i, txhash in enumerate(transaction_hashs)}

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


class TraceParser:
    def __init__(self, mix_trace_tree: MixTraceTree):
        self.mix_tree = mix_trace_tree
        self.external_ops = {"CALL", "STATICCALL", "DELEGATECALL", "CALLCODE", "CREATE", "CREATE2"}

    def is_external(self, item: MixTraceItem) -> bool:
        if item.call_type in self.external_ops:
            return True
        if item.from_addr != item.to_addr and item.call_type != "":
            return True
        return False

    def build_simulator(self) -> 'DebugSimulator':
        external_root = self._build_ext_node_recursive(self.mix_tree.root, depth=0)

        external_node_map = {}

        def _assign_index(node: TraceNode):
            external_node_map[node.pos] = node
            for child in node.children:
                _assign_index(child)

        if external_root:
            _assign_index(external_root)
        return DebugSimulator(external_root, external_node_map)

    def _build_ext_node_recursive(self, item: MixTraceItem, depth: int) -> TraceNode:
        current_node = TraceNode(item, depth, node_type="EXTERNAL")

        for child_item in item.calls:
            found_externals = self._find_first_level_externals(child_item, depth + 1)
            current_node.children.extend(found_externals)

        return current_node

    def _find_first_level_externals(self, item: MixTraceItem, depth: int) -> List[TraceNode]:
        if self.is_external(item):
            return [self._build_ext_node_recursive(item, depth)]

        results = []
        for child in item.calls:
            results.extend(self._find_first_level_externals(child, depth))
        return results

    def get_internal_flow(self, abs_pos: int) -> Optional[TraceNode]:
        target_item = self.mix_tree.get_node(abs_pos)
        if not target_item:
            return None

        def _build_flow(item: MixTraceItem, current_depth: int, is_root: bool) -> TraceNode:
            is_ext = self.is_external(item)
            node = TraceNode(item, current_depth, node_type="EXTERNAL" if is_ext else "INTERNAL")

            if is_ext and not is_root:
                return node

            for child in item.calls:
                node.children.append(_build_flow(child, current_depth + 1, False))
            return node

        return _build_flow(target_item, 0, True)


class TraceRender:
    def __init__(self, root_node: 'TraceNode'):
        self.root_node = root_node

    def render(self) -> str:
        if not self.root_node:
            return "No execution flow available."

        lines = [f"Showing Internal Flow for Position: {self.root_node.pos}"]

        def _recursive_render(node, prefix, is_last):
            if node == self.root_node:
                connector = " "
                new_prefix = ""
            else:
                connector = "└── " if is_last else "├── "
                new_prefix = prefix + ("    " if is_last else "│   ")

            node_info = node.format_node()
            lines.append(f"{prefix}{connector}{node_info}")

            if node.children:
                child_count = len(node.children)
                for i, child in enumerate(node.children):
                    _recursive_render(child, new_prefix, i == (child_count - 1))
            elif node.node_type == "EXTERNAL" and node != self.root_node:
                lines.append(f"{new_prefix}└── ... (External execution omitted) ...")

        _recursive_render(self.root_node, "", True)
        return "\n".join(lines)
