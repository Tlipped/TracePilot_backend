import asyncio
import sys
import uuid
import traceback
from typing import Dict, List, Optional
from fastmcp import FastMCP
from tqdm import tqdm

from daos.tenderly import MixTraceTree
from entities.trace import MixTraceItem
from process.trace.trace import TraceLoader, TraceParser, TraceRender
from process.trace.debug_simulator import DebugSimulator
from settings import SCAN_APIKEYS, JSONRPCS
from utils.bucket import AsyncItemBucket

# Tools: [expand_node, collapse_node, get_function_source_code, get_contract_source_code,
#         get_mixed_execution_flow, get_node_detailed_state, update_comments,
#         update_understanding, ready_for_patch, switch_transaction]
from utils.evm_utils import EVMTools

mcp = FastMCP("TraceDebuggerServer")


class GlobalResourceManager:
    def __init__(self):
        self.apikey_buckets = {}
        self.rpc_buckets = {}

    def get_apikey_bucket(self, platform: str):
        if platform not in self.apikey_buckets:
            self.apikey_buckets[platform] = AsyncItemBucket(items=SCAN_APIKEYS.get(platform, []), qps=1)
        return self.apikey_buckets[platform]

    def get_rpc_bucket(self, platform: str):
        if platform not in self.rpc_buckets:
            self.rpc_buckets[platform] = AsyncItemBucket(items=JSONRPCS.get(platform, []), qps=1)
        return self.rpc_buckets[platform]


global_resources = GlobalResourceManager()


class SessionContext:
    def __init__(self, session_id: str, platform: str):
        self.session_id = session_id
        self.platform = platform

        self.current_tx: Optional[str] = None
        self.tx_list: List[str] = []

        self.active_sessions: Dict[str, DebugSimulator] = {}
        self.tx2micro: Dict[str, MixTraceTree] = {}
        self.tx2init: Dict[str, str] = {}
        self.loader: Optional[TraceLoader] = None


class TraceSessionManager:
    def __init__(self):
        self._sessions: Dict[str, SessionContext] = {}
        self._lock = asyncio.Lock()

    async def create_session(self, platform: str) -> str:
        session_id = str(uuid.uuid4())
        async with self._lock:
            self._sessions[session_id] = SessionContext(session_id, platform)
        print(f"✅ Session created: {session_id} (Platform: {platform})", file=sys.stderr)
        return session_id

    def get_session(self, session_id: str) -> Optional[SessionContext]:
        return self._sessions.get(session_id)

    async def destroy_session(self, session_id: str):
        async with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                print(f"🗑️ Session destroyed: {session_id}", file=sys.stderr)


session_manager = TraceSessionManager()


def _get_session_context(session_id: str) -> SessionContext:
    session = session_manager.get_session(session_id)
    if not session:
        raise ValueError(f"Session '{session_id}' not found or expired. Please init session first.")
    return session


def _get_debugger(session: SessionContext) -> DebugSimulator:
    if not session.current_tx:
        raise ValueError("Current transaction is not set in this session.")

    debugger = session.active_sessions.get(session.current_tx)
    if not debugger:
        raise ValueError(f"Debugger for transaction {session.current_tx} not initialized.")
    return debugger


def _get_mix_tree(session: SessionContext) -> MixTraceTree:
    if not session.current_tx:
        raise ValueError("Current transaction is not set.")

    tree = session.tx2micro.get(session.current_tx)
    if not tree:
        raise ValueError(f"MixTraceTree for {session.current_tx} not found.")
    return tree


def _get_target_tree(session: SessionContext, tx_hash: str) -> MixTraceTree:
    if not tx_hash:
        raise ValueError("Transaction hash is empty.")
    tx_hash = tx_hash.lower()

    if tx_hash in session.tx2micro:
        tx2micro = session.tx2micro.get(tx_hash)
        if not tx2micro:
            raise ValueError(f"MixTraceTree not found for {tx_hash}.")
    else:
        raise ValueError(f"Transaction {tx_hash} is not in the session.")
    return tx2micro


# --- MCP Tools ---

@mcp.tool()
async def init_debug_session(dapp: Dict) -> Dict:
    """
    init Trace Debugger.
    entry function，return session_id.
    Args:
        dapp: fault issue
    """
    try:
        _platform = dapp.get("platform", "Ethereum")  # Default to eth if missing
        session_id = await session_manager.create_session(_platform)
        session = session_manager.get_session(session_id)

        apikey_bucket = global_resources.get_apikey_bucket(_platform)
        rpc_bucket = global_resources.get_rpc_bucket(_platform)

        raw_tx_list = list(set(dapp.get("transaction_hash", [])))
        tx_list = [str(tx).lower() for tx in raw_tx_list]
        session.tx_list = tx_list

        # Load traces
        trace_loader = TraceLoader(tx_list, apikey_bucket=apikey_bucket, rpc_bucket=rpc_bucket, _platform=_platform)
        await trace_loader.load_properties()
        tx2micro = await trace_loader.load_micro_data()

        session.tx2micro = tx2micro
        session.loader = trace_loader

        for tx_hash in tqdm(tx_list, total=len(tx_list), desc=f"[{session_id[:8]}] Init Debuggers"):
            mix_trace_tree = session.tx2micro.get(tx_hash)
            if mix_trace_tree:
                debugger = TraceParser(mix_trace_tree).build_simulator()
                session.active_sessions[tx_hash] = debugger
                session.tx2init[tx_hash] = debugger.render()

        if tx_list:
            session.current_tx = tx_list[0]

        return {
            "session_id": session_id,
            "tx2init": session.tx2init,
            "current_tx": session.current_tx
        }
    except Exception as e:
        return {"error": f"Failed to init session: {str(e)}", "traceback": traceback.format_exc()}


@mcp.tool()
async def expand_node(session_id: str, index: int, depth: int = 1) -> str:
    """
    Expand the specified node in the Trace tree.
    - index: The sequence number value preceding each Trace node, such as [203821]. Please expand the Trace tree strictly according to the numerical value preceding the node.
    """
    try:
        session = _get_session_context(session_id)
        debugger = _get_debugger(session)
        debugger.expand_node(index, depth)
        return f"Expanded node {index} for tx {session.current_tx}."
    except Exception as e:
        return f"Error expanding node: {str(e)}"


@mcp.tool()
async def collapse_node(session_id: str, index: int) -> str:
    """
    Contract the specified node in the Trace tree. When analyzing a subtree and no new findings are made, if the subtree contains a large number of repetitive features or is irrelevant to the fault core, call this tool to contract the subtree. If the analysis process results in an excessively long Trace tree, the system will automatically initiate the contraction task.
    If there are significant features, changes in key states, important discoveries, etc. in the subtree to be contracted, call the `update_comments` tool to update the features and discoveries in the subtree to the comments of the index node.
    """
    try:
        session = _get_session_context(session_id)
        debugger = _get_debugger(session)
        debugger.collapse_node(index)
        return f"Collapsed node {index} for tx {session.current_tx}."
    except Exception as e:
        return f"Error collapsing node: {str(e)}"


@mcp.tool()
async def get_function_source_code(session_id: str, index: int) -> str:
    """
    Obtain the source code information of the specified index node (either by calling an external function or by jumping to an internal function)
    """
    try:
        session = _get_session_context(session_id)
        mix_tree = _get_mix_tree(session)

        mix_trace_item = mix_tree.node_map.get(index)
        if not mix_trace_item:
            return f"Error: Node {index} not found in mix trace tree."

        return (
            f"File: {mix_trace_item.contract_name}\n"
            f"Contract: {mix_trace_item.address}\n"
            f"Function Name: {mix_trace_item.function_name}\n"
            f"Call Site (Caller): {mix_trace_item.caller_source}\n"
            f"--------------------------------------------------\n"
            f"{mix_trace_item.function_source}"
        )
    except Exception as e:
        return f"Error getting function source: {str(e)}"


@mcp.tool()
async def get_contract_source_code(session_id: str, index: int) -> str:
    """
    Obtain the complete smart contract code information of the function call node at the specified index.
    """
    try:
        session = _get_session_context(session_id)
        debugger = _get_debugger(session)

        node = debugger.node_map.get(index)
        if not node:
            return f"Error: Node {index} not found."

        if not node.mix_trace_item.contract_source:
            return f"No source files found for address {node.mix_trace_item.address}."

        return node.mix_trace_item.contract_source
    except Exception as e:
        return f"Error getting contract source: {str(e)}"


@mcp.tool()
async def get_mixed_execution_flow(session_id: str, index: int) -> str:
    """
    Obtain the complete mixed execution flow of the specified Trace node.
    Functions:
    1. Present the code execution path in chronological order.
    2. Integrate the display of internal function calls (Internal Calls) and external contract calls (External Calls).
    3. Show the changes in key state variables (SSTORE).
    Usage scenario:
    Use this when you suspect there is a problem with the internal logic of a function (such as if/else branches, loops, or the sequence of internal function calls).
    """
    try:
        session = _get_session_context(session_id)
        mix_trace_tree = _get_mix_tree(session)

        root_node = TraceParser(mix_trace_tree).get_internal_flow(index)
        return TraceRender(root_node).render()

    except Exception as e:
        return f"Error generating flow: {str(e)}\n{traceback.format_exc()}"


@mcp.tool()
async def get_node_detailed_state(session_id: str, index: int) -> str:
    """
    Obtain a complete state snapshot (State Snapshot) of the specified Trace node.
    It includes: input parameters, output return values, snapshots of variables before and after the call (Caller/Function Variables), as well as underlying storage changes (Storage) and events (Logs).
    Parameter:
    index: The unique identifier of the node
    """
    try:
        session = _get_session_context(session_id)
        mix_tree = _get_mix_tree(session)

        if index not in mix_tree.node_map:
            return f"Error: Index {index} not found in current transaction tree."

        item: MixTraceItem = mix_tree.node_map.get(index)

        is_proxy_call = item.address.lower() != item.to_addr.lower()

        lines = [f"### State Details for Node [{index}]",
                 f"**Logic Contract:** {item.contract_name} ({item.to_addr})",
                 f"**Storage Context:** {item.address}" + (" (Proxy)" if is_proxy_call else ""),
                 f"**Function:** `{item.function_name}`",
                 f"**Call Type:** {item.call_type}", "---", "#### [I/O Interface]",
                 f"- **Decoded Input:** {item.decoded_input if item.decoded_input else 'None'}",
                 f"- **Decoded Output:** {item.decoded_output if item.decoded_output else 'None'}", "",
                 "#### [Variable Snapshots]"]

        if item.caller_variables:
            lines.append("**Caller Context Variables (State before/during call):**")
            for var in item.caller_variables:
                lines.append(f"  - `{var.type} {var.name} = {var.value}`")

        if item.function_variables:
            lines.append("**Function Internal Variables (State during/after call):**")
            for var in item.function_variables:
                lines.append(f"  - `{var.type} {var.name} = {var.value}`")

        if not item.caller_variables and not item.function_variables:
            lines.append("- No high-level variable snapshots available.")
        lines.append("")

        lines.append("#### [Storage Operations (SLOAD/SSTORE)]")
        if item.storage_ops:
            for op in item.storage_ops:
                op_label = "🔴 SSTORE (Write)" if op.call_type == "SSTORE" else "🔵 SLOAD (Read)"
                loc = f"Slot {op.slot}"
                if op.call_type == "SSTORE":
                    change = f"`{op.val_before}` -> `{op.val_after}`"
                    status = " (Changed)" if op.is_changed() else " (No Change)"
                else:
                    change = f"`{op.val_before}`"
                    status = ""
                lines.append(f"- {op_label} {loc}")
                lines.append(f"  Value: {change}{status}")
                if op.source_line and op.source_snippet:
                    lines.append(op.get_code())
        else:
            lines.append("- No direct storage operations in this node.")
        lines.append("")

        lines.append("#### [Emitted Events]")
        if item.events:
            for log in item.events:
                vars_str = ", ".join([f"{v.name}={v.value}" for v in log.variables])
                lines.append(f"- **{log.event_name}**({vars_str})")
        else:
            lines.append("- No events emitted.")

        return "\n".join(lines)

    except Exception as e:
        return f"Error getting detailed state: {str(e)}"


@mcp.tool()
async def update_comments(session_id: str, comments_json: dict) -> str:
    """
    Update the comments in the debugger (call this function each time a new subtree is expanded), and return the comments for the key function calls in the form of a dictionary. The key is the index identifier before the function call, and the value is the marked comment.
    Just list the newly added or modified comments. The existing comments are saved on the MCP server and do not need to be repeatedly output.
    """
    try:
        session = _get_session_context(session_id)
        debugger = _get_debugger(session)

        debugger.update_comments(comments_json)
        return "Comments updated."
    except Exception as e:
        return f"Error updating comments: {str(e)}"


@mcp.tool()
async def update_understanding(insight: str) -> str:
    """
    The new findings or data supplements to the existing discoveries will be incorporated into the overall understanding of DApp failures. These insights will be sent to the Global Memory Administrator. The updated overall understanding of failures will be input after being organized.
    """
    return "Added to the global DApp fault understanding update queue"


@mcp.tool()
async def ready_for_patch(fault_report: str, fix_report: str) -> str:
    """
    After completing the debugging process of this round of transactions, based on the generated pseudo-code, fault reports and comments, initiate the PoC verification process. :param:
    fault_report: Generate a detailed DApp failure analysis report, providing a fine-grained understanding of DApp failures at the function call level, offering real and specific data support for each inference and analysis, and demonstrating which function call supports which inference.
    fix_report: Generate a complete and formatted DApp failure repair report according to the template, identifying the type of vulnerability in the DApp failure, and providing a targeted root cause description, logical guidance for repair, and suggestions for modifying the core repair points for each faulty function.
    """
    return "The Patch verification process has been initiated. Please wait for the verification result."


@mcp.tool()
async def get_current_tree(session_id: str) -> str:
    """
    Obtain the current Trace tree structure
    """
    try:
        session = _get_session_context(session_id)
        debugger = _get_debugger(session)
        return debugger.render()
    except Exception as e:
        return f"Error in render: {str(e)}"


@mcp.tool()
async def set_current_tx(session_id: str, tx_hash: str) -> str:
    """
    Set the hash of the current transaction being processed
    """
    try:
        session = _get_session_context(session_id)
        if tx_hash not in session.active_sessions:
            return f"Error: Transaction {tx_hash} is not initialized in this session."

        session.current_tx = tx_hash
        return f"Current transaction set to {tx_hash}"
    except Exception as e:
        return f"Error setting current tx: {str(e)}"


@mcp.tool()
async def get_patch_items(session_id: str, faulty_index: str) -> Dict:
    """
    Based on the index, obtain the source code information and compilation configuration of the specified node, which is used for patch generation and compilation.
    """
    try:
        session = _get_session_context(session_id)
        faulty = faulty_index.split("::")
        tx_hash = faulty[0]
        index = faulty[-1]

        mix_trace_tree = _get_target_tree(session, tx_hash)
        index = int(index)

        item = mix_trace_tree.node_map.get(index, None)
        if item and item.to_addr:
            contracts = mix_trace_tree.contracts
            item = mix_trace_tree.node_map.get(index, None)

            contract_address = item.to_addr.lower()
            contract = contracts.get(contract_address, None)
            if contract is None:
                return {}

            file_index = -1
            if hasattr(item, "function_loc") and item.function_loc:
                file_index = item.function_loc.file_index

            return {
                "address": contract_address,
                "file_index": file_index,
                "contract_info": contract.contract_info,
                "contract_name": contract.contract_name,
                "compiler_version": contract.compiler_version,
                "evm_version": contract.evm_version,
                "optimizations_used": contract.optimizations_used,
                "optimization_runs": contract.optimization_runs,
                "compiler_settings": contract.compiler_settings,
                "libraries": contract.libraries
            }
        return {}
    except Exception as e:
        print(f"Error in get_patch_items: {e}", file=sys.stderr)
        return {}


@mcp.tool()
async def switch_transaction(session_id: str, target_tx_hash: str, insight: str, code_patch: str) -> str:
    """
    End the current transaction debugging and switch to the specified transaction for analysis. Use this when you believe the current transaction has been fully analyzed, or when you need to view other transactions to confirm the source of funds/status.
    **Important: Before invoking this tool, you must provide a complete and detailed summary of the current transaction. These summary details will be saved and passed on to the analysis process of subsequent transactions, which is crucial for building a comprehensive understanding of the faults! **

    :param:
    target_tx_hash: The transaction hash of the target transaction to be switched to (the transactions in transaction_need_analyze should be analyzed first. If you need to view other transactions to obtain additional information, you can also call this tool). A complete and existing transaction hash must be provided; otherwise, an error will occur.
    Insight: **It is necessary to summarize the analysis results of the current transaction in detail**, including the following contents (please organize the information according to the following structure to ensure completeness):
    1. **Core transaction operations**: Extract the core operations of the current transaction and divide them into multiple stages. Each stage needs to describe the key operations and the function call indices involved.
    2. **Key function calls**: List all the critical exploit function calls, including:
    - Function signatures and call indices (index)
    - Key parameter values and their meanings
    - The functions' roles and impacts
    3. **Root cause analysis of vulnerabilities**: If a exploited vulnerability is found in this transaction, describe in detail the type of the vulnerability discovered, the triggering conditions, and the exploitation method, including:
    - Vulnerability type (such as: absence of access control, price manipulation, re-entry attack, etc.)
    - Specific code location and logical flaws of the vulnerability
    - How the attacker exploits the vulnerability
    4. **Summary of key findings**: Summarize the most important findings in this transaction. These findings will help analyze subsequent transactions.
    code_patch: **A detailed patch plan for the current attack transaction must be provided**, including the following contents:
    1. **Identification of Vulnerable Functions**: List all the functions that need to be fixed along with their indices (transaction_hash + index)
    2. **Repair Strategy**: For each vulnerable function, explain:
    - The cause of the code failure
    - The specific repair solution (such as: adding access control, modifying calculation logic, adding re-entry protection, etc.)
    - The expected effect after the repair
    3. **Patch Code Snippet**: Provide the core repair code snippets (use '+' to mark added code, '-' to mark deleted code)
    4. **Description of Protection Scope**: Explain the types and scenarios of attacks that this patch plan can protect against
    Note:
    - If you want to switch to the auxiliary trading for analysis, please supplement the information that needs to be obtained in the auxiliary trading in the insight. Once the information is fully obtained or if you cannot find it, switch back.
    - The content of insight and code_patch should be as detailed and complete as possible. These information will be saved and used in the subsequent analysis of transactions.
    - If the current transaction analysis is not in-depth enough, it is recommended to continue the analysis before switching, rather than switching hastily.
    """
    try:
        session = _get_session_context(session_id)
        target_tx_hash = target_tx_hash.lower()

        if target_tx_hash not in session.tx_list:
            return "Error: Target transaction can't find."

        if target_tx_hash not in session.active_sessions:
            return f"Error: Target transaction {target_tx_hash} not loaded in session."

        prev_tx = session.current_tx
        session.current_tx = target_tx_hash

        return f"Switched from {prev_tx} to {target_tx_hash}. Insight recorded."
    except Exception as e:
        return f"Error switching transaction: {str(e)}"


@mcp.tool()
async def analyze_unverified_contract(session_id: str, index: int) -> str:
    """
    Conduct a deep code analysis of the unverified contracts (Unverified/Bytecode-only) in the Trace tree.
    Tool capabilities:
    1. Use 'Heimdall-rs' (local tool) to decompile EVM bytecode into pseudo Solidity code, restoring the business logic.
    2. Use 'OpenChain' database to identify function selectors and discover contract functions from the bytecode.
    When to use:
    - When Trace displays `Unverified_0x...` and you need to know what this function is doing.
    - When the contract has no source code, but you need to determine if it has backdoors or vulnerabilities.
    """
    try:
        session = _get_session_context(session_id)
        mix_tree = _get_mix_tree(session)

        node_item = mix_tree.node_map.get(index)
        if not node_item:
            return f"Error: Node {index} not found."

        target_address = node_item.to_addr.lower()

        contract_entity = mix_tree.contracts.get(target_address)
        bytecode = None

        if contract_entity:
            if contract_entity.deployed_bytecode and contract_entity.deployed_bytecode != "0x":
                bytecode = contract_entity.deployed_bytecode
            elif contract_entity.data and contract_entity.data.get("bytecode"):
                bytecode = contract_entity.data.get("bytecode")

        if not bytecode and node_item.call_type in ["CREATE", "CREATE2"]:
            bytecode = node_item.input_data

        if not bytecode or bytecode == "0x":
            return f"Unable to fetch bytecode for address {target_address}. "

        analysis_report = await EVMTools.analyze_bytecode(bytecode)

        header = f"### Analysis Report for {target_address}\n"
        return header + analysis_report

    except Exception as e:
        import traceback
        return f"Error analyzing unverified contract: {str(e)}\n{traceback.format_exc()}"


@mcp.tool()
async def close_session(session_id: str) -> str:
    """Close and clean up the specified session"""
    try:
        await session_manager.destroy_session(session_id)
        return f"Session {session_id} closed successfully"
    except Exception as e:
        return f"Error closing session: {str(e)}"


if __name__ == "__main__":
    mcp.run()
