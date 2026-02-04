import asyncio
from typing import List, Dict, Set, Tuple, Callable

import networkx as nx
from networkx import MultiDiGraph, DiGraph

from daos.contract import ContractDao, ContractCompileItem
from daos.money import MoneyTransferDao, TransferItem
from daos.trace import PCTraceItem, PCTraceDao, TraceItem, TraceDao
from downloaders.contract import ContractSourceDownloader
from downloaders.trace import PCTraceDownloader, FlatTraceDownloader
from downloaders.trans import TransactionDownloader
from utils.bucket import AsyncItemBucket
from utils.diffusion import leak_diffusion
from utils.solc import SourceMappingItem
from utils.typed_ast import get_ast_graph

CALL_OPS = {
    'CALL': True, 'CALLCODE': True,
    'STATICCALL': True, 'DELEGATECALL': True,
    'CREATE': True, 'CREATE2': True,
    'SELFDESTRUCT': True,
}


class DAppFunctionGraph:
    def __init__(
            self, rpc_bucket: AsyncItemBucket,
            apikey_bucket: AsyncItemBucket,
            transfer_graph: DiGraph,
            gamma: float = 0.1, epsilon: float = 1e-3,
            grad_func: Callable = lambda x: x ** 3
    ):
        self.rpc_bucket = rpc_bucket
        self.apikey_bucket = apikey_bucket
        self.transfer_graph = transfer_graph
        self.gamma = gamma
        self.epsilon = epsilon
        self.grad_func = grad_func

    async def process(self, dapp: Dict) -> nx.MultiDiGraph:
        # load all trace data
        txhashs = dapp['transaction_hash']
        txhash2pc_list = await self._load_pctrace_data(txhashs)
        txhash2trace = await self._load_trace_data(txhashs)

        # load all compilation result and source code (fault only)
        contract_addresses = set()
        for _txhash in txhashs:
            pc_list = txhash2pc_list[_txhash]
            contract_addresses.update([pc_item.address for pc_item in pc_list])

        addr2source = await self._load_source_code(contract_addresses)
        addr2compilation = await self._load_compile_result(contract_addresses)

        # load ast nodes
        related_addr_and_fn = set()
        addr_pc2source_map = dict()
        for addr, compilation in addr2compilation.items():
            for item in compilation.source_mapping:
                addr_pc = '{}#{}'.format(addr, item.pc)
                addr_pc2source_map[addr_pc] = item
        for pc_list in txhash2pc_list.values():
            for pc_item in pc_list:
                addr_pc = '{}#{}'.format(pc_item.address, pc_item.pc)
                mapping_item = addr_pc2source_map.get(addr_pc)
                if mapping_item is None:
                    continue
                related_addr_and_fn.add((pc_item.address, mapping_item.filename))
        ast_graph = await self._load_ast_graph(related_addr_and_fn, addr2compilation)

        # init func nodes
        function_nodes = {
            node: True for node, attr in ast_graph.nodes(data=True)
            if attr.get('type') == 'FunctionDefinition'
        }

        # build target graph with in slice flow
        graph = nx.MultiDiGraph()
        for idx, txhash in enumerate(txhashs):
            graph = self._process_tx(
                graph=graph, tx_index=idx,
                pc_list=txhash2pc_list[txhash],
                trace_list=txhash2trace[txhash],
                addr_pc2source_map=addr_pc2source_map,
                function_nodes=function_nodes,
            )

        # attach source code for all function
        for nid in graph.nodes():
            graph.nodes[nid]['type'] = 'FunctionDefinition'
            location = nid.split('#')
            if len(location) == 3:
                addr, fn, src = location
                begin, offset = [int(offset) for offset in src.split(':')]
                code = addr2source[addr][fn][begin: begin + offset]
                graph.nodes[nid]['code'] = code
                continue
            code = 'function %s{ // This function lacks implementation }'
            code = code % location[3]
            graph.nodes[nid]['code'] = code

        # set transfer involved attr
        transfer_involved_addrs = set()
        for _, attr in self.transfer_graph.nodes(data=True):
            transfer: TransferItem = attr['info']
            transfer_involved_addrs.add(transfer.from_address)
            transfer_involved_addrs.add(transfer.to_address)
        for n in graph.nodes():
            addr = n.split('#')[0]
            if addr not in transfer_involved_addrs:
                continue
            graph.nodes[n]['transfer_involved'] = True

        # set money leakage score
        transfer_idx2score = await self._leak_diffusion(self.transfer_graph)
        pc_item_and_score = [
            (self.transfer_graph.nodes[idx]['info'].pc_item, score)
            for idx, score in transfer_idx2score.items()
        ]
        for pc_item, score in pc_item_and_score:
            addr_pc = '{}#{}'.format(pc_item.address, pc_item.pc)
            mapping_item = addr_pc2source_map.get(addr_pc)
            if mapping_item is None:
                continue

            addr_fn = '{}#{}'.format(pc_item.address, mapping_item.filename)
            for n in function_nodes:
                if not n.startswith(addr_fn) or n not in graph.nodes:
                    continue
                _, _, src = n.split('#')
                begin, offset = src.split(':')
                begin, end = int(begin), int(begin) + int(offset)
                if mapping_item.begin >= begin and \
                        mapping_item.begin + mapping_item.offset <= end:
                    graph.nodes[n]['leakage'] = graph.nodes[n].get('leakage', 0) + score
                    break

        # map fault location to graph
        for location in dapp['fault'].get('location', list()):
            address, filename, src = location.split('#')
            fault_begin, fault_offset = [int(offset) for offset in src.split(':')]
            fault_end = fault_begin + fault_offset
            addr_fn = '{}#{}'.format(address, filename)
            for n in graph.nodes():
                if not n.startswith(addr_fn) or len(n.split('#')) > 3:
                    continue
                _, _, src = n.split('#')
                begin, offset = src.split(':')
                begin, end = int(begin), int(begin) + int(offset)
                if fault_begin >= begin and fault_end <= end:
                    graph.nodes[n]['is_fault'] = True
        return graph

    def _process_tx(
            self, graph: nx.MultiDiGraph, tx_index: int,
            pc_list: List[PCTraceItem], trace_list: List[TraceItem],
            addr_pc2source_map: Dict[str, SourceMappingItem],
            function_nodes: Dict[str, bool],
    ) -> nx.MultiDiGraph:
        # spilt pcs with call ops
        pc_slices = list()
        start_idx = 0
        for i, pc_item in enumerate(pc_list):
            if not CALL_OPS.get(pc_item.opcode):
                continue
            pc_slices.append(pc_list[start_idx: i])
            start_idx = i + 1
        pc_slices.append(pc_list[start_idx: len(pc_list)])

        # add function calls
        call_index = 0
        pre_target = None
        for sidx in range(len(pc_slices)):
            if len(pc_slices[sidx]) == 0:
                continue

            # parse functions in the slice
            funcs = list()
            for pc_item in pc_slices[sidx]:
                addr_pc = '{}#{}'.format(pc_item.address, pc_item.pc)
                mapping_item = addr_pc2source_map.get(addr_pc)
                if mapping_item is None:
                    continue
                ast_node = '{}#{}#{}:{}'.format(
                    pc_item.address, mapping_item.filename,
                    mapping_item.begin, mapping_item.offset,
                )
                if not function_nodes.get(ast_node) or \
                        (len(funcs) > 0 and ast_node == funcs[-1]):
                    continue
                funcs.append(ast_node)

            # add function call edge
            trace_item = trace_list[sidx]
            if all([
                len(funcs) == 0,  # no source code
                len(trace_item.data_input) > 2,  # is call function
            ]):
                funcs.append('{}###{}'.format(
                    trace_item.address_to,
                    trace_item.data_input[: 2 + 8],
                ))
            if len(funcs) == 0:
                continue
            if pre_target is not None:
                graph.add_edge(
                    pre_target, funcs[0],
                    type=trace_item.ctype.lower(),
                    tx_index=tx_index,
                    call_index=call_index,
                    value=trace_item.value,
                    gas=trace_item.gas,
                    gas_used=trace_item.gas_used,
                )
                # print('{}_{}_{}: {} -> {}'.format(
                #     tx_index,
                #     trace_item.ctype.lower(),
                #     call_index,
                #     pre_target, funcs[0],
                # ))
                call_index += 1
            for i in range(1, len(funcs)):
                graph.add_edge(
                    funcs[i - 1], funcs[i],
                    type='jump',
                    tx_index=tx_index,
                    call_index=call_index,
                )
                # print('{}_{}_{}: {} -> {}'.format(
                #     tx_index,
                #     'jump', call_index,
                #     funcs[i - 1], funcs[i],
                # ))
                call_index += 1

            pre_target = funcs[-1]

        return graph

    async def _leak_diffusion(self, transfer_graph: nx.DiGraph) -> Dict[int, float]:
        addr2token2in = dict()
        addr2token2profit = dict()
        for node, attr in transfer_graph.nodes(data=True):
            item: TransferItem = attr['info']

            token2in = addr2token2in.get(item.to_address, dict())
            in_transfers = token2in.get(item.contract, list())
            in_transfers.append(item)
            token2in[item.contract] = in_transfers
            addr2token2in[item.to_address] = token2in

            token2profit = addr2token2profit.get(item.to_address, dict())
            token2profit[item.contract] = token2profit.get(item.contract, 0) + item.amount
            addr2token2profit[item.to_address] = token2profit

            token2profit = addr2token2profit.get(item.from_address, dict())
            token2profit[item.contract] = token2profit.get(item.contract, 0) - item.amount
            addr2token2profit[item.from_address] = token2profit

        # filter
        for addr, token2in in addr2token2in.items():
            for token, in_transfers in token2in.items():
                in_amount = sum([t.amount for t in in_transfers])
                if addr2token2profit[addr][token] / in_amount < 0.05:
                    addr2token2in[addr][token] = list()
                    continue

                unswapped_transfers = list()
                for transfer in in_transfers:
                    edges = transfer_graph.out_edges(transfer.index)
                    if len(edges) != 0:
                        continue
                    unswapped_transfers.append(transfer)
                addr2token2in[addr][token] = unswapped_transfers

        # collect
        sources = set()
        for addr, token2in in addr2token2in.items():
            for token, in_transfers in token2in.items():
                for transfer in in_transfers:
                    sources.add(transfer.index)

        # execute leak diffusion
        return leak_diffusion(
            graph=transfer_graph,
            sources=sources,
            gamma=self.gamma,
            epsilon=self.epsilon,
            grad_func=self.grad_func,
        )

    async def _load_pctrace_data(self, transaction_hashs: List[str]) -> Dict[str, List[PCTraceItem]]:
        async def _create_task(_transaction_hash: str):
            return await PCTraceDao(downloader=PCTraceDownloader(
                rpc_url=await self.rpc_bucket.get(),
            )).get_pc_list(transaction_hash=_transaction_hash)

        tasks = [_create_task(txhash) for txhash in transaction_hashs]
        result = await asyncio.gather(*tasks)
        txhash2pc_list = dict()
        for i, txhash in enumerate(transaction_hashs):
            txhash2pc_list[txhash] = result[i]
        return txhash2pc_list

    async def _load_trace_data(self, transaction_hashs: List[str]) -> Dict[str, List[TraceItem]]:
        async def _create_task(_transaction_hash: str):
            return await TraceDao(downloader=FlatTraceDownloader(
                rpc_url=await self.rpc_bucket.get(),
            )).get_call_list(transaction_hash=_transaction_hash)

        tasks = [_create_task(txhash) for txhash in transaction_hashs]
        result = await asyncio.gather(*tasks)
        txhash2trace = dict()
        for i, txhash in enumerate(transaction_hashs):
            txhash2trace[txhash] = result[i]
        return txhash2trace

    async def _load_compile_result(self, contract_addresses: Set[str]) -> Dict[str, ContractCompileItem]:
        async def _create_task(_address: str):
            return await ContractDao(downloader=ContractSourceDownloader(
                apikey=await self.apikey_bucket.get(),
            )).get_compile_item(_address)

        # load compile result of all contract
        tasks = [_create_task(addr) for addr in contract_addresses]
        result = await asyncio.gather(*tasks)
        return {addr: result[i] for i, addr in enumerate(contract_addresses)}

    async def _load_ast_graph(
            self, related_addr_and_fn: List[Tuple[str, str]],
            addr2compilation: Dict[str, ContractCompileItem]
    ) -> nx.MultiDiGraph:
        graph = nx.MultiDiGraph()

        async def _create_task(_address: str, _fn: str):
            ast = addr2compilation[_address].ast
            if not ast.get(_fn):
                return
            g = await get_ast_graph(ast[_fn])
            nodes = dict()
            for n, attr in g.nodes(data=True):
                begin, offset, _ = attr['src'].split(':')
                src = '{}:{}'.format(begin, offset)
                node_name = '{}#{}#{}'.format(_address, _fn, src)
                attr['filename'] = _fn
                nodes[n] = (node_name, attr)
            graph.add_nodes_from(nodes.values())
            graph.add_edges_from([
                (nodes[u][0], nodes[v][0], dict(type='child', order=attr['order']))
                for u, v, attr in g.edges(data=True)
            ])

        # load all result
        tasks = [_create_task(addr, fn) for addr, fn in related_addr_and_fn]
        await asyncio.gather(*tasks)
        return graph

    async def _load_source_code(self, contract_addresses: Set[str]) -> Dict[str, Dict[str, str]]:
        async def _create_task(_address: str):
            return await ContractDao(downloader=ContractSourceDownloader(
                apikey=await self.apikey_bucket.get(),
            )).get_source_code(_address)

        # load source code of all contract
        tasks = [_create_task(addr) for addr in contract_addresses]
        result = await asyncio.gather(*tasks)
        return {addr: result[i] for i, addr in enumerate(contract_addresses)}


class DAppTransferGraph(DAppFunctionGraph):
    def __init__(self, rpc_bucket: AsyncItemBucket, _platform: str, property_dict: Dict):
        super().__init__(rpc_bucket, None, None)
        self._platform = _platform
        self.property_dict = property_dict

    async def process(self, dapp: Dict) -> tuple[MultiDiGraph, DiGraph]:
        # load all pc
        txhashs = dapp['transaction_hash']
        txhash2pc_list = await self._load_pctrace_data(txhashs)

        # add money features for the ast nodes
        pc_list = list()
        for txhash in txhashs:
            pc_list.extend(txhash2pc_list[txhash])
        address_flow_graph, transfer_event_graph = await self._load_money_graph(
            transaction_hash_list=txhashs,
            pc_list=pc_list,
            tx2pc_list=txhash2pc_list
        )
        return address_flow_graph, transfer_event_graph

    async def _load_money_graph(
            self, transaction_hash_list: List[str],
            pc_list: List[PCTraceItem],
            tx2pc_list: Dict
    ) -> tuple[MultiDiGraph, DiGraph]:
        return await MoneyTransferDao(downloader=TransactionDownloader(
            rpc_url=await self.rpc_bucket.get(),
        ), rpc_bucket=self.rpc_bucket, tx2pc_list=tx2pc_list, property_dict=self.property_dict).get_transfer_graph(
            transaction_hash_list=transaction_hash_list,
            pc_list=pc_list,
            _platform=self._platform
        )
