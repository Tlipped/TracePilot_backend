import asyncio
import sys
import time
from typing import List, Optional, Dict, Set

import aiohttp

from daos.contract import ContractDao
from downloaders.defs import Downloader
from downloaders.trace import TenderlyFullSimulationDownloader, TenderlySimulateDownloader, \
    TenderlyBundleSimulateDownloader
from entities.contract import ContractEntity
from entities.trace import MixTraceItem, Log, StorageOp
from settings import PLATFORM_TO_CHAIN_ID
from utils.balance_utils import BalanceAnalyzer, fetch_token_prices
from utils.price import platform_to_chain
from utils.signature import hex_to_int


class MixTraceTree:

    def __init__(self, root: Dict, contracts: List[Dict], addr2name: Dict):
        enricher = TraceSourceEnricher(contracts, addr2name)
        self.contracts: Dict[str, ContractEntity] = enricher.entity_map
        self.root = enricher.enrich_tree(root)
        self.node_map: Dict[int, MixTraceItem] = {}  # absolute_position -> MixTraceItem
        self._build_map(self.root)

    def _build_map(self, node: MixTraceItem):
        if node.absolute_position != -1:
            self.node_map[node.absolute_position] = node

        for child in node.calls:
            self._build_map(child)

    def get_node(self, pos: int) -> Optional[MixTraceItem]:
        return self.node_map.get(pos)

    def get_all_positions(self) -> List[int]:
        return sorted(list(self.node_map.keys()))


class TraceSourceEnricher:
    def __init__(self, contracts: List[Dict], addr2name: Dict):
        safe_contracts = contracts or []
        # address -> ContractEntity
        self.entity_map: Dict[str, ContractEntity] = {
            c.get("address", "").lower(): ContractEntity.from_dict(c)
            for c in safe_contracts
        }
        # address -> name
        self.addr2name = addr2name

    def enrich_tree(self, root_data: Dict) -> MixTraceItem:

        def _is_call_type(call_type: str) -> bool:
            call_types = {"CALL", "STATICCALL", "DELEGATECALL", "CREATE", "CREATE2", "CALLCODE", "JUMPDEST", "JUMP"}
            return call_type in call_types

        def _recursive_build(data: Dict, parent: Optional[MixTraceItem] = None, depth: int = 0) -> MixTraceItem | None:
            if not data:
                return None

            call_type = data.get('call_type', '')

            node = MixTraceItem(data, parent, depth)
            to_addr = node.to_addr.lower()
            from_addr = node.from_addr.lower()
            context_addr = node.address.lower()
            node.from_name = self.addr2name.get(from_addr, "")
            node.to_name = self.addr2name.get(context_addr) or self.addr2name.get(to_addr, "")

            # SLOAD/SSTORE/LOG
            if not _is_call_type(call_type) and parent is not None:
                if call_type in ["SLOAD", "SSTORE"]:
                    item = StorageOp(data)
                    item.source_snippet = self._get_snippet(
                        parent.to_addr.lower(), item.loc.file_index, item.loc.code_start, item.loc.code_length
                    )
                    item.source_line = self._get_line(parent.to_addr.lower(), item.loc.file_index, item.loc.line_number)
                    parent.storage_ops.append(item)
                elif call_type.startswith("LOG"):
                    item = Log(data)
                    item.source_snippet = self._get_snippet(
                        parent.to_addr.lower(), item.loc.file_index, item.loc.code_start, item.loc.code_length
                    )
                    parent.events.append(item)
                return None

            for _input in node.decoded_input:
                _input.decoded_value = self.addr2name.get(str(_input.value), "")

            for _output in node.decoded_output:
                _output.decoded_value = self.addr2name.get(str(_output.value), "")

            contracts = self.entity_map.get(to_addr)
            if contracts:
                contract_type, description = contracts.get_contract_status()

                if contract_type == "FULLY_OPEN_SOURCE":
                    file_idx = node.function_loc.file_index
                    if contracts and file_idx in contracts.file_map:
                        node.contract_source = contracts.file_map[file_idx]
                    if node.function_loc.code_start != -1:
                        node.function_source = self._get_snippet(
                            to_addr, node.function_loc.file_index, node.function_loc.code_start,
                            node.function_loc.code_length
                        )
                elif contract_type == "BYTECODE_ONLY":
                    node.contract_source = (f"Unverified Contract (Bytecode Only).\n"
                                            f"Tip: Use `analyze_unverified_contract(session_id, {node.absolute_position})` "
                                            f"to decompile and inspect logic.")
                    node.function_source = "Source not available. Only Bytecode."
                else:
                    node.contract_source = description
                    node.function_source = description

            if parent and parent is not None and node.caller_loc.code_start != -1:
                node.caller_source = self._get_snippet(
                    parent.to_addr, node.caller_loc.file_index, node.caller_loc.code_start, node.caller_loc.code_length
                )

            raw_calls = data.get('calls') or []
            if isinstance(raw_calls, list):
                for sub_data in raw_calls:
                    child_node = _recursive_build(sub_data, node, depth + 1)
                    if child_node:
                        node.calls.append(child_node)
            return node

        return _recursive_build(root_data)

    def _get_snippet(self, addr: str, file_idx: int, start: int, length: int) -> str:
        entity = self.entity_map.get(addr.lower())
        if not entity or file_idx not in entity.file_map:
            return ""
        source_code = entity.file_map[file_idx]
        if not source_code or start == -1:
            return ""
        encoded_source = source_code.encode('utf-8')
        byte_end = start + length
        snippet_bytes = encoded_source[start:byte_end]
        try:
            return snippet_bytes.decode('utf-8')
        except UnicodeDecodeError:
            return snippet_bytes.decode('utf-8', errors='replace')

    def _get_line(self, addr: str, file_idx: int, line: int) -> str:
        entity = self.entity_map.get(addr.lower())
        if not entity or file_idx not in entity.file_map:
            return ""

        source_code = entity.file_map[file_idx]
        if not source_code or line < 1:
            return ""

        try:
            lines = source_code.splitlines()
            if 1 <= line <= len(lines):
                return lines[line - 1]
        except Exception:
            return ""

        return ""


class TenderlyDao:
    def __init__(self, _platform, tx_downloader, source_downloader: Downloader, bytecode_downloader, creation_downloader: Downloader, addr2property: Dict):
        self.chain_id = PLATFORM_TO_CHAIN_ID.get(_platform, 1)
        self.tx_downloader = tx_downloader if tx_downloader else None
        self.source_downloader = source_downloader
        self.id_downloader = TenderlySimulateDownloader()
        self.result_downloader = TenderlyFullSimulationDownloader()
        self.bytecode_downloader = bytecode_downloader
        self.creation_downloader = creation_downloader
        self.addr2property = addr2property

    async def send_request(self, payload) -> Dict:
        max_attempts = 3
        full_result = None
        for attempt in range(1, max_attempts + 1):
            try:
                simulation_id = await self.id_downloader.download(payload=payload)
                if not simulation_id:
                    raise RuntimeError("Tenderly simulation id is empty")

                full_result = await self.result_downloader.download(simulation_id=simulation_id)
                if full_result and 'transaction' in full_result and 'contracts' in full_result:
                    break

                print(
                    f"Tenderly retry {attempt}/{max_attempts}: incomplete simulation result "
                    f"for tx {payload.get('transaction_hash')}"
                )
            except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                print(
                    f"Tenderly retry {attempt}/{max_attempts}: request failed for tx "
                    f"{payload.get('transaction_hash')}: {type(exc).__name__} - {exc}"
                )
            except Exception as exc:
                print(
                    f"Tenderly retry {attempt}/{max_attempts}: simulation failed for tx "
                    f"{payload.get('transaction_hash')}: {type(exc).__name__} - {exc}"
                )

            if attempt < max_attempts:
                await asyncio.sleep(min(2 ** attempt, 8))

        if not full_result or 'transaction' not in full_result or 'contracts' not in full_result:
            print(f"Error: No data found for payload {payload}")
            return {}
        return full_result

    async def simulate_tx_in_tenderly(self, transaction_hash: str):
        tx_info = await self.tx_downloader.download(transaction_hash=transaction_hash)

        payload = {
            "transaction_hash": transaction_hash,
            "network_id": self.chain_id,
            "from": tx_info.get("from", ""),
            "to": tx_info.get("to", ""),
            "input": tx_info.get("input", ""),
            "gas": hex_to_int(tx_info.get("gas", "")),
            "gas_price": hex_to_int(tx_info.get("gasPrice", "")),
            "value": hex_to_int(tx_info.get("value", "")),
            "block_number": hex_to_int(tx_info.get("blockNumber", "")),
            "save": True,
            "save_if_fails": True,
            "simulation_type": "full",
            "generate_state_diff": True,
            "generate_visual_trace": True,
            "cache": True
        }
        full_result = await self.send_request(payload)
        if not full_result:
            return {}, {}, {}

        transaction = full_result.get('transaction', {})
        contracts = full_result.get('contracts', [])

        addresses = transaction.get('addresses', [])
        contracts = await self._supplement_missing_contracts(addresses, contracts)
        addr2name = await self._load_contract_name(set(addresses))
        addr2name.update(self.addr2property)

        return transaction, contracts, addr2name

    async def deploy_contracts_on_tenderly(self, _addr: str, _creation_bytecode: str) -> tuple[str, str | None]:
        # add constructor arguments
        result = await self.source_downloader.download(contract_address=_addr)
        constructor_arguments = result.get("ConstructorArguments", "")
        if constructor_arguments.startswith("0x"):
            constructor_arguments = constructor_arguments[2:]
        _creation_bytecode = _creation_bytecode + constructor_arguments

        if not _creation_bytecode.startswith("0x"):
            _creation_bytecode = f"0x{_creation_bytecode}"

        sim_from = "0x1804c8AB1F12E6bbf3894d4083f33e07309d1f38"
        sim_value = 0
        sim_gas = 30000000
        sim_gas_price = 0
        sim_block_number = None

        try:
            creation_info = await self.creation_downloader.download(contract_address=_addr)

            if creation_info:
                creator_address = creation_info.get("creator")
                tx_hash = creation_info.get("tx_hash")

                if tx_hash:
                    tx_detail = await self.tx_downloader.download(transaction_hash=tx_hash)

                    if tx_detail:
                        sim_from = tx_detail.get("from", creator_address)
                        sim_block_number = hex_to_int(tx_detail.get("blockNumber"))
                        sim_value = hex_to_int(tx_detail.get("value", "0x0"))
                        sim_gas = hex_to_int(tx_detail.get("gas", "0x1C9C380"))
                        sim_gas_price = hex_to_int(tx_detail.get("gasPrice", "0x0"))

                        print(f"[Deploy Context] Found original tx {tx_hash}: Block {sim_block_number}, From {sim_from}, Value {sim_value}")
        except Exception as e:
            print(f"[Deploy Context Warning] Failed to fetch original deployment info for {_addr}: {e}. Using defaults.")

        payload = {
            "network_id": str(self.chain_id),
            "from": sim_from,
            "input": _creation_bytecode,
            "gas": sim_gas,
            "gas_price": sim_gas_price,
            "value": sim_value,
            "simulation_type": "full",
            "save": True,
            "save_if_fails": True,
            "cache": False
        }
        if sim_block_number:
            payload["block_number"] = sim_block_number

        try:
            full_result = await self.send_request(payload)
            transaction = full_result.get('transaction', {})
            status = transaction.get('status', False)
            tx_info = transaction.get('transaction_info', {})

            if not status:
                error_msg = transaction.get('error_message', 'Unknown Error')
                stack_trace_raw = tx_info.get('stack_trace', [])
                formatted_trace = self._format_tenderly_stack_trace(stack_trace_raw)
                detailed_error = (
                    f"[DEPLOY_ERROR] Deployment Simulation Failed for address {_addr}\n"
                    f"Basic Error: {error_msg}\n"
                    f"Constructor Args: {constructor_arguments}\n"
                    f"--- Stack Trace Detail ---\n"
                    f"{formatted_trace}\n"
                    f"--------------------------\n"
                )
                print(f"Deploy failed for {_addr}: {error_msg}")
                return _addr, detailed_error

            call_trace = tx_info.get('call_trace', {})
            runtime_bytecode = call_trace.get('output', "")

            if not runtime_bytecode or runtime_bytecode == "0x":
                msg = f"[DEPLOY_ERROR] Transaction success but NO Runtime Bytecode generated for {_addr}."
                print(msg)
                return _addr, msg

            return _addr, runtime_bytecode

        except Exception as e:
            error_report = f"[DEPLOY_ERROR] Exception during tenderly deploy simulation for {_addr}: {str(e)}"
            print(error_report)
            return _addr, error_report

    async def get_micro_data(self, transaction_hash: str) -> MixTraceTree | None:
        res = await self.simulate_tx_in_tenderly(transaction_hash)
        if not res:
            return None
        transaction, contracts, addr2name = res

        transaction_info = transaction.get('transaction_info', {})
        call_trace = transaction_info.get('call_trace')
        if not call_trace:
            print(f"Error: call_trace is empty for hash {transaction_hash}")
            return None

        mix_trace_tree = MixTraceTree(call_trace, contracts, addr2name)
        return mix_trace_tree

    def _format_tenderly_stack_trace(self, stack_trace: list) -> str:
        if not stack_trace:
            return "No stack trace available."

        lines = []
        for i, frame in enumerate(stack_trace):
            file_index = frame.get('file_index', '?')
            line_number = frame.get('line', '?')
            func_name = frame.get('name', 'constructor/anonymous')
            error_reason = frame.get('error_reason')  # Revert string
            error_type = frame.get('error')  # Error type info
            code_raw = frame.get('code')
            code_snippet = code_raw.strip() if code_raw else ""

            if error_reason or error_type:
                marker = ">>> [REVERT HERE]"
            else:
                marker = f"[{i}]"
            frame_str = f"{marker} File:{file_index} Line:{line_number} | Function: {func_name}"

            if error_reason:
                frame_str += f"\n    REVERT REASON: {error_reason}"
            if error_type:
                frame_str += f"\n    ERROR TYPE: {error_type}"

            if code_snippet:
                preview = code_snippet[:200] + "..." if len(code_snippet) > 200 else code_snippet
                frame_str += f"\n    CODE: {preview}"

            lines.append(frame_str)

        return "\n".join(lines)

    async def _supplement_missing_contracts(
            self,
            involved_addresses: List[str],
            existing_contracts: List[Dict]
    ) -> List[Dict]:
        existing_addrs = {c.get('address', '').lower() for c in existing_contracts}

        missing_addrs = [
            addr.lower() for addr in involved_addresses
            if addr.lower() not in existing_addrs
               and addr.lower() != "0x0000000000000000000000000000000000000000"
               and not (addr.lower().startswith("0x00000000000000000000000000000000000000") and len(addr) == 42)
        ]

        if not missing_addrs:
            return existing_contracts

        contract_dao = ContractDao(downloader=self.source_downloader)

        async def fetch_and_format(addr: str) -> Optional[Dict]:
            try:
                raw_data = await self.source_downloader.download(contract_address=addr)

                has_source = False
                if raw_data and isinstance(raw_data, dict):
                    source_code_field = raw_data.get("SourceCode", "")
                    if source_code_field and "not verified" not in source_code_field.lower():
                        has_source = True

                if has_source:
                    source_map = await contract_dao.get_source_code(addr)
                    if source_map:
                        mock_contract_info = []
                        for i, (path, content) in enumerate(source_map.items()):
                            mock_contract_info.append({
                                "id": i,
                                "path": path if path else f"{raw_data.get('ContractName', 'Contract')}.sol",
                                "source": content
                            })

                        return {
                            "id": f"ext-{addr}",
                            "address": addr,
                            "contract_name": raw_data.get("ContractName", "Unknown"),
                            "public": True,
                            "compiler_version": raw_data.get("CompilerVersion", ""),
                            "optimizations_used": raw_data.get("OptimizationUsed") == "1",
                            "optimization_runs": int(raw_data.get("Runs", 200)),
                            "data": {
                                "contract_info": mock_contract_info,
                                "abi": []
                            }
                        }
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"Source fetch failed for {addr}. Attempting to fetch bytecode...")

            bytecode = await self.bytecode_downloader.download(contract_address=addr)

            if bytecode:
                # print(f"Successfully fetched bytecode for {addr}", file=sys.stderr)
                return {
                    "id": f"bytecode-{addr}",
                    "address": addr,
                    "contract_name": f"Unverified_{addr[:8]}",
                    "public": False,
                    "deployed_bytecode": bytecode,
                    "creation_bytecode": "",
                    "contract_id": f"unverified-{addr}",
                    "data": {
                        "contract_info": [],
                        "abi": []
                    }
                }

            print(f"Failed to fetch both source and bytecode for {addr}")
            return None

        supplemental_data = await asyncio.gather(*(fetch_and_format(a) for a in missing_addrs))
        valid_supplements = [d for d in supplemental_data if isinstance(d, dict)]
        return existing_contracts + valid_supplements

    async def _load_contract_name(self, contract_addresses: Set[str]) -> Dict[str, Dict[str, str]]:
        addr_list = list(contract_addresses)
        semaphore = asyncio.Semaphore(5)

        async def _create_task(_address: str):
            async with semaphore:
                return await ContractDao(downloader=self.source_downloader).get_contract_name(_address)

        tasks = [_create_task(addr) for addr in addr_list]
        result = await asyncio.gather(*tasks)
        return {addr_list[i]: result[i] for i in range(len(addr_list))}


class BundleTenderlyDao:
    def __init__(self, _platform):
        self.bundle_downloader = TenderlyBundleSimulateDownloader()
        self.chain_id = PLATFORM_TO_CHAIN_ID.get(_platform, 1)
        self.chain_slug = platform_to_chain.get(_platform, "ethereum")

    async def bundle_simulate_txs_in_tenderly(self, transactions, addr2bytecode):
        payload = self.prepare_bundle_payload(transactions, addr2bytecode)
        try:
            simulation_results = await self.bundle_downloader.download(payload=payload)

            if not simulation_results or not isinstance(simulation_results, list):
                error_msg = f"Tenderly API returned invalid response: {simulation_results}"
                return False, error_msg

            if isinstance(simulation_results, dict) and 'error' in simulation_results:
                return False, f"Tenderly API Error: {simulation_results.get('error')}"
        except Exception as e:
            import traceback
            return False, f"Tenderly API Exception: {str(e)}\n{traceback.format_exc()}"

        # parse simulate results
        replay_reports = []
        for i, simulation_result in enumerate(simulation_results):
            detail = transactions[i]
            transaction = simulation_result.get('transaction', {}) or {}
            simulation = simulation_result.get('simulation', {}) or {}

            status = simulation.get("status", False) or False
            error_msg = simulation.get("error_message", "No Error")

            # balance change
            transaction_info = transaction.get("transaction_info", {}) or {}
            timestamp = detail.get("timestamp", int(time.time()))

            raw_changes, metadata = BalanceAnalyzer.parse_tenderly_data(transaction_info)
            balance_report = "No balance data."

            if any(raw_changes.values()):
                involved_tokens = set()
                for user_bals in raw_changes.values():
                    involved_tokens.update(user_bals.keys())

                prices = await fetch_token_prices(self.chain_slug, timestamp, involved_tokens)
                balance_report = BalanceAnalyzer.calculate_usd_values_markdown(raw_changes, metadata, prices)

            report_item = (
                f"Transaction Index: {i}\n"
                f"Original Tx Hash: {detail.get('hash')}\n"
                f"Execution Status: {'SUCCESS' if status else 'REVERT'}\n"
                f"Error Message: {error_msg}\n"
                f"{balance_report}\n"
                f"--------------------------------------------------"
            )
            replay_reports.append(report_item)
        full_report = "\n".join(replay_reports)
        return True, full_report

    def prepare_bundle_payload(self, attack_txs: List[Dict], addr2bytecode: Dict[str, str]):
        payload = {"simulations": [], "contracts": []}
        state_objects = {}
        for address, bytecode in addr2bytecode.items():
            if bytecode:
                state_objects[address.lower()] = {"code": bytecode if bytecode.startswith("0x") else f"0x{bytecode}"}

        for tx in attack_txs:
            simulation = {
                "network_id": self.chain_id,
                "from": tx.get("from"),
                "to": tx.get("to"),
                "input": tx.get("input_data"),
                "value": tx.get("value", {}).get("wei", "0"),
                "block_number": tx.get("block_number", 0) - 1,
                "simulation_type": "full",
                "save": True,
                "save_if_fails": True,
                "generate_state_diff": True,
                "generate_visual_trace": True,
            }
            if state_objects and state_objects != {}:
                simulation["state_objects"] = state_objects
            payload["simulations"].append(simulation)
        return payload
