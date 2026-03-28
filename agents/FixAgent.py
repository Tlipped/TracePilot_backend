import ast
import asyncio
import json
from typing import Dict, List, Tuple, Union

from agents.AgentBase import AgentBase
from daos.contract import ContractDao, ContractCompileItem
from daos.tenderly import BundleTenderlyDao, TenderlyDao
from downloaders.contract import ContractSourceDownloader, ContractBytecodeDownloader, ContractCreationDownloader
from downloaders.trans import BlockTimestampDownloader, TxDownloader
from entities.contract import ContractEntityForCompile
from mcp_tools.mcp_client import MCPClient
from prompt.fix_prompt import FIX_SP, FIX_UP, FIX_FIX_UP
from settings import PLATFORM_TO_CHAIN_ID, FIX_TURN
from utils.bytecode_transplanter import BytecodeTransplanter
from utils.patch import SolidityCodePatcher


class FixAgent(AgentBase):
    def __init__(self, processed_data, apikey_bucket, rpc_bucket, mcp_client: MCPClient, dapp_name: str,
                 session_id: str, name="Code Patcher", metrics_collector=None, log_callback=None):
        super(FixAgent, self).__init__(name, FIX_SP, unique_id=dapp_name, max_turns=1,log_callback=log_callback)
        self.processed_data = processed_data
        self.apikey_bucket = apikey_bucket
        self.rpc_bucket = rpc_bucket
        self.mcp_client = mcp_client
        self.session_id = session_id
        self.unique_id = dapp_name
        self._platform = self.processed_data.get("dapp", {}).get("platform", "Ethereum")
        self.chain_id = PLATFORM_TO_CHAIN_ID.get(self._platform)
        self.metrics_collector = metrics_collector

    async def handle(self, fault_data: Dict):
        fix_report = fault_data.get("fix_report", {})
        json_parse_success, parse_result = self.process_fix_report(fix_report)
        if not json_parse_success:
            return False, parse_result
        fix_report = parse_result

        faulty_indexes = []
        faulty_functions = fix_report.get("faulty_functions", [])
        for faulty_function in faulty_functions:
            if "trigger_point" in faulty_function and faulty_function.get("trigger_point") != {}:
                trigger_point = faulty_function.get("trigger_point")
                if "index" in trigger_point and trigger_point.get("index") \
                        and "transaction_hash" in trigger_point and trigger_point.get("transaction_hash"):
                    index = trigger_point.get("index", None)
                    transaction_hash = trigger_point.get("transaction_hash", None)
                    faulty_indexes.append(f"{transaction_hash}::{index}")

        patch_error = ""
        address2contracts: Dict[str, ContractEntityForCompile] = {}
        uid2faults: Dict[str, list] = {}  # {unique_id: [indexes]}
        uid2source: Dict[str, str] = {}  # {unique_id: source_code}
        for faulty_index in faulty_indexes:
            patch_item = await self.mcp_client.call_tool("get_patch_items",
                                                         {"session_id": self.session_id, "faulty_index": faulty_index})
            print(f"========================\npatch_item:\n{patch_item}========================\n")

            if isinstance(patch_item, str):
                print(f"[FixAgent] Warning: get_patch_items failed for {faulty_index}. Error: {patch_item}")
                patch_error = patch_error + "\n" + patch_item
                continue

            if patch_item == {}:
                continue

            address = patch_item.get("address")
            if address and address != "":
                contract_info = patch_item.get("contract_info", [])

                has_source = len(contract_info) > 0

                if not has_source:
                    if patch_item.get("deployed_bytecode"):
                        error_msg = f"\n[FixAgent] Error: Contract at {patch_item.get('address')} is Unverified (Bytecode Only). Automatic patching requires Source Code."
                        print(error_msg)
                        patch_error += error_msg
                    else:
                        print(f"[FixAgent] Warning: No contract info found for {faulty_index}")
                    continue

                full_file_map: Dict[int, str] = {
                    f.get("id"): {
                        "path": f.get("path", ""),
                        "name": f.get("name", ""),
                        "source": f.get("source", "")
                    } for f in contract_info
                }
                sources: Dict[str, str] = {
                    f.get("path"): f.get("source", "") for f in contract_info
                }

                if address not in address2contracts:
                    patch_item["full_file_map"] = full_file_map
                    patch_item["sources"] = sources
                    address2contracts[address] = ContractEntityForCompile.from_dict(patch_item)

                file_index = patch_item.get("file_index", -1)
                target_file_info = full_file_map.get(file_index, {})

                if file_index != -1 and target_file_info != {}:
                    file_path = target_file_info.get("path", "Unknown.sol")
                    source_code = target_file_info.get("source", "")
                    unique_id = f"{address}::{file_path}"
                    uid2source[unique_id] = source_code

                    if unique_id not in uid2faults:
                        uid2faults[unique_id] = []
                    uid2faults[unique_id].append(faulty_index)

        format_contracts = self.get_format_contracts(uid2faults, uid2source) if patch_error == "" else patch_error

        original_compile_results = await self._load_compile_result(address2contracts)
        on_chain_bytecodes = await self._load_runtime_bytecode(list(address2contracts.keys()))

        patch_execution_success = False
        patches = ""
        last_error = fault_data.get("fix_feedback") if len(fault_data.get("fix_feedback", "")) > 0 else ""
        max_retries = FIX_TURN
        retry_count = 0
        final_replay_logs = ""

        while not patch_execution_success and retry_count < max_retries:

            if last_error == "":
                patches = await self.query(FIX_UP.format(
                    fault_report=fault_data.get("fault_report", ""),
                    final_trace=fault_data.get("final_trace", ""),
                    fix_report=fault_data.get("fix_report", ""),
                    contract_code=format_contracts,
                    last_error=last_error
                ), _format='str', temperature=1.0)
            else:
                patches = await self.query(FIX_FIX_UP.format(
                    fault_report=fault_data.get("fault_report", ""),
                    fix_report=fault_data.get("fix_report", ""),
                    contract_code=format_contracts,
                    last_error=last_error
                ), _format='str', temperature=1.0)
            parse_success, parse_logs, patched_contracts = SolidityCodePatcher(uid2source).apply_patches(patches)

            if not parse_success:
                retry_count += 1
                last_error = (
                    f"Patch Application Failed (Format/Context Error):\n{parse_logs}\n"
                    f"Action Required: Please strictly follow the Unified Diff format or "
                    f"ensure the original code context matches exactly."
                )
                print(f"[FixAgent] Retry {retry_count}/{max_retries} due to Parsing Error: {parse_logs}")
                continue

            updated_contracts = self._update_contracts_with_patch(address2contracts, patched_contracts)

            # compile patched code (Creation Bytecode)
            compile_success, compile_result_or_error = await self.compile_patched_codes(updated_contracts)

            if self.metrics_collector:
                self.metrics_collector.record_compile_status(
                    self.unique_id,
                    compile_success
                )

            if not compile_success:
                retry_count += 1
                last_error = f"Compilation Failed:\n{compile_result_or_error}"
                print(f"[FixAgent] Retry {retry_count}/{max_retries} due to Compilation Error: {last_error}")
                continue

            # ======================================================================================
            # Core Logic: Runtime Bytecode Generation (Dual Strategy)
            # Strategy A: Bytecode Transplantation (Direct Immutable Mapping)
            # Strategy B: Simulation Deployment (Creation Bytecode + Args -> Tenderly)    backup
            # ======================================================================================

            addr2runtime_bytecode = {}
            strategy_b_candidates = {}  # Store items that failed Strategy A
            bytecode_generation_error = ""

            # --- Step A: Attempt Bytecode Transplantation ---
            for addr, patched_compile_item in compile_result_or_error.items():
                original_item = original_compile_results.get(addr)
                on_chain_code = on_chain_bytecodes.get(addr)

                if not original_item:
                    bytecode_generation_error += f"Missing original compile result for {addr}\n"
                    continue

                # Try Strategy A
                is_ok, final_bytecode, logs = BytecodeTransplanter.transplant(
                    original_compile_item=original_item,
                    on_chain_runtime_bytecode_hex=on_chain_code,
                    patched_compile_item=patched_compile_item
                )

                if is_ok:
                    addr2runtime_bytecode[addr] = final_bytecode
                    print(f"[FixAgent] Strategy A (Transplant) successful for {addr}")
                else:
                    print(f"[FixAgent] Strategy A failed for {addr}: {logs}. Fallback to Strategy B.")
                    strategy_b_candidates[addr] = patched_compile_item

            # --- Step B: Attempt Tenderly Simulation Deployment (Fallback) ---
            if len(strategy_b_candidates) > 0:
                addr2creation_bytecode = {}
                for addr, compile_item in strategy_b_candidates.items():
                    creation_bytecode = compile_item.creation_bytecode
                    if not creation_bytecode:
                        print(f"[FixAgent] Warning: No creation bytecode for {addr}, skipping Strategy B.")
                        continue
                    addr2creation_bytecode[addr] = creation_bytecode

                print(f"[FixAgent] Executing Strategy B (Tenderly Deploy) for: {list(addr2creation_bytecode.keys())}")
                try:
                    deployed_results = await self._deploy_contracts(addr2creation_bytecode)

                    for addr, runtime_code in deployed_results.items():
                        if runtime_code and len(runtime_code) > 2:
                            addr2runtime_bytecode[addr] = runtime_code
                            print(f"[FixAgent] Strategy B successful for {addr}")
                        else:
                            error_msg = f"Strategy B failed for {addr}: Tenderly returned empty bytecode. Error Message: {deployed_results}"
                            print(f"[FixAgent] {error_msg}")
                            bytecode_generation_error += error_msg + "\n"

                except Exception as e:
                    error_msg = f"Strategy B Exception: {str(e)}"
                    print(f"[FixAgent] {error_msg}")
                    bytecode_generation_error += error_msg + "\n"

            missing_bytecode_addresses = []
            for addr in compile_result_or_error.keys():
                if addr not in addr2runtime_bytecode:
                    missing_bytecode_addresses.append(addr)

            if len(missing_bytecode_addresses) > 0:
                retry_count += 1
                error_details = bytecode_generation_error if bytecode_generation_error else "Unknown deployment error"

                last_error = (
                    f"Runtime Bytecode Generation Failed for addresses: {missing_bytecode_addresses}.\n"
                    f"This means the patched code could not be deployed or transplanted.\n"
                    f"Potential reasons: Constructor reverted during deployment, or Immutable variables mismatch.\n"
                    f"Detailed Error Logs:\n{error_details}"
                )
                print(f"[FixAgent] Retry {retry_count}/{max_retries} (Bytecode Gen Error): {last_error}")
                continue

            try:
                tenderly_transactions = await self.prepare_bundle_data()
                execution_success, replay_logs = await self.run_patch_verification_from_tenderly(
                    tenderly_transactions, addr2runtime_bytecode
                )

                if not execution_success:
                    retry_count += 1
                    last_error = f"Tenderly Simulation API Failed (Infrastructure Error or Revert). Logs: {replay_logs}\nAction: Please check logic or bytecode size."
                    print(f"[FixAgent] Retry {retry_count}/{max_retries}: Verification Failed. {last_error}")
                    continue
                else:
                    patch_execution_success = True
                    final_replay_logs = replay_logs
                    print(f"Patch applied and simulated successfully. Handing over to Judge. Revert reason: {replay_logs}.")
                    break
            except Exception as e:
                retry_count += 1
                last_error = f"Exception during verification preparation: {str(e)}"
                print(f"[FixAgent] Retry {retry_count}/{max_retries}: Exception: {str(e)}")
                continue

        return patch_execution_success, final_replay_logs, patches, retry_count

    def _update_contracts_with_patch(
            self,
            original_contracts: Dict[str, ContractEntityForCompile],
            patched_contracts: Dict[str, str]
    ) -> Dict[str, ContractEntityForCompile]:
        import copy
        contracts_copy = copy.deepcopy(original_contracts)

        for unique_id, patched_code in patched_contracts.items():
            if "::" not in unique_id: continue
            addr, file_path = unique_id.split("::", 1)

            if not patched_code:
                continue

            if addr in contracts_copy:
                entity = contracts_copy[addr]

                if entity.full_file_map:
                    for file_id, file_info in entity.full_file_map.items():
                        if file_info.get("path") == file_path:
                            entity.full_file_map[file_id]["source"] = patched_code

                if entity.sources and file_path in entity.sources:
                    entity.sources[file_path] = patched_code
        return contracts_copy

    async def prepare_bundle_data(self):
        tx_list = self.processed_data.get("transaction_hash_list", [])
        tx2detail = self.processed_data.get("transaction_to_detail", {})
        attack_transactions = self.processed_data.get("attack_transactions", [])

        block_numbers = [detail["block_number"] for tx, detail in tx2detail.items()]
        block2time = await self._load_transaction_time(block_numbers)

        tenderly_transactions = []
        for tx in tx_list:
            if tx in attack_transactions:
                detail = tx2detail.get(tx)
                if detail:
                    block = detail["block_number"]
                    timestamp = block2time.get(block)
                    if not timestamp:
                        raise ValueError(
                            f"CRITICAL: Failed to fetch timestamp for block {block}. Cannot proceed.")
                    detail["timestamp"] = timestamp
                    tenderly_transactions.append(detail)
        return tenderly_transactions

    async def run_patch_verification_from_tenderly(self, transactions, addr2bytecode):
        verification_success, verification_result = await BundleTenderlyDao(
            _platform=self._platform).bundle_simulate_txs_in_tenderly(transactions, addr2bytecode)
        return verification_success, verification_result

    async def compile_patched_codes(self, patched_contracts):
        """
        compile patched contracts
        """
        if patched_contracts == {}:
            return False, "Patched contract is none. Patch application is wrong."

        for addr, entity in patched_contracts.items():
            if not entity:
                return False, f"Pre-compile Check Failed: Entity for address {addr} is None."

            if not hasattr(entity, 'sources') or not entity.sources:
                return False, f"Pre-compile Check Failed: Contract {addr} has no 'sources' dictionary. It might be an unverified contract."

            has_valid_code = False
            for file_path, source_code in entity.sources.items():
                if source_code and len(source_code.strip()) > 0:
                    has_valid_code = True
                    break

            if not has_valid_code:
                return False, (
                    f"Pre-compile Check Failed: Contract {addr} contains file entries but source code is empty. "
                    f"This usually happens when attempting to patch a Bytecode-Only contract.")

        addr2compile = await self._load_compile_result(patched_contracts)

        for addr, compile_item in addr2compile.items():
            has_bytecode = compile_item.bytecode and len(compile_item.bytecode) > 2 \
                           and compile_item.creation_bytecode and len(compile_item.creation_bytecode) > 2

            if not has_bytecode:
                error_lines = [f"Compilation failed for address: {addr}"]
                if compile_item.errors:
                    for err in compile_item.errors:
                        if isinstance(err, str):
                            error_lines.append(err)
                            continue
                        msg = err.get("formattedMessage") or err.get("message", "Unknown Error")
                        severity = err.get("severity", "unknown")
                        error_lines.append(f"[{severity.upper()}] {msg}")
                else:
                    error_lines.append("Reason: Unknown (No bytecode generated and no error logs returned).")
                return False, "\n".join(error_lines)
        return True, addr2compile

    async def _load_compile_result(self, address2contracts) -> Dict[str, ContractCompileItem]:
        async def _create_task(_contracts: ContractEntityForCompile):
            return await ContractDao(downloader=ContractSourceDownloader(apikey="")).get_compile_item_from_entity(
                _contracts)

        tasks = [_create_task(contracts) for addr, contracts in address2contracts.items()]
        result = await asyncio.gather(*tasks)
        return {addr: result[i] for i, addr in enumerate(address2contracts.keys())}

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

    async def _load_runtime_bytecode(self, contract_addresses: List[str]) -> Dict[str, str]:
        async def _create_task(_address):
            return await ContractBytecodeDownloader(
                rpc_url=await self.rpc_bucket.get(),
            ).download(contract_address=_address)

        tasks = [_create_task(contract_address) for contract_address in contract_addresses]
        results = await asyncio.gather(*tasks)
        addr2runtime_bytecode = dict()
        for i, contract_address in enumerate(contract_addresses):
            addr2runtime_bytecode[contract_address] = results[i]
        return addr2runtime_bytecode

    async def _deploy_contracts(self, addr2creation_bytecode: Dict[str, str]):
        async def _create_task(_addr: str, _bytecode: str):
            return await TenderlyDao(
                _platform=self._platform,
                tx_downloader=TxDownloader(rpc_url=await self.rpc_bucket.get()),
                source_downloader=ContractSourceDownloader(apikey=await self.apikey_bucket.get()),
                bytecode_downloader=ContractBytecodeDownloader(rpc_url=await self.rpc_bucket.get()),
                creation_downloader=ContractCreationDownloader(apikey=await self.apikey_bucket.get()),
                addr2property={}
            ).deploy_contracts_on_tenderly(_addr, _bytecode)

        tasks = [_create_task(addr, creation_bytecode) for addr, creation_bytecode in addr2creation_bytecode.items()]
        result = await asyncio.gather(*tasks)
        return {original_addr: runtime_code for original_addr, runtime_code in result}

    def get_format_contracts(self, uid2faults: Dict[str, List], uid2source: Dict[str, str]):
        lines = ["=" * 60]
        for unique_id, source_code in uid2source.items():
            fault_indexes = uid2faults.get(unique_id, [])
            lines.append(f"# File: {unique_id}")
            lines.append(f"Faulty functions in this file: {fault_indexes}")
            lines.append("-" * 20)
            lines.append(source_code)
            lines.append("=" * 60)
            lines.append("\n")
        return "\n".join(lines)

    def process_fix_report(self, fix_report) -> Tuple[bool, Dict]:
        if fix_report is None:
            return False, {}
        if isinstance(fix_report, dict):
            return True, fix_report
        fix_report = (
            str(fix_report)
            .replace('\xa0', ' ')
            .replace('\r\n', '\n')
            .replace('\u200b', '')
            .strip()
        )
        parse_result = self.parser.process_response(fix_report, _format="json")
        if not isinstance(parse_result, dict):
            return self.final_process(parse_result)
        if "result" in parse_result:
            return self.final_process(parse_result["result"])
        return True, parse_result

    def final_process(self, parse_result):
        try:
            parse_result = json.loads(parse_result)
        except json.JSONDecodeError:
            try:
                parse_result = ast.literal_eval(parse_result)
            except Exception:
                return False, "can't parse fix report"
        return True, parse_result
