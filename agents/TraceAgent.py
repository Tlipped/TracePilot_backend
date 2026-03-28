import ast
import json
import re
from typing import Dict

from agents.AgentBase import AgentBase
from prompt.trace_debug_prompt import (
    TRACE_DEBUG_SP, TRACE_DEBUG_UP_INIT, TRACE_DEBUG_UP_LATER,
    TRACE_DEBUG_FORCE_PATCH_UP,
    TRACE_SWITCH_TX_UP, MULTI_ATTACK_PART, TRACE_DEBUG_FORCE_COLLAPSE_UP
)
from settings import (
    TX_DEBUG_TURN,
    TX_DEBUG_MAX_IDLE_TURNS,
)
from utils.llm import MODEL_MAX_OUTPUT_TOKENS


class TraceAgent(AgentBase):
    def __init__(self, processed_data, mcp_client, dapp_name,
                 name="Transaction Debugger", log_callback=None):
        super(TraceAgent, self).__init__(name, "", need_tool=True, unique_id=dapp_name,log_callback=log_callback)
        self.dapp = processed_data["dapp"]
        self.processed_data = processed_data
        self.tx_list = [tx.lower() for tx in self.dapp["transaction_hash"]]
        # ———————————————————————————————————
        self.mcp_client = mcp_client
        # ————————————————————————————————————
        self.current_tx = ""
        self.is_init = True
        self.transaction_insights = {}
        self.session_id = None

        self._debug_turn_left = TX_DEBUG_TURN
        self._idle_turns = 0

    def _inject_session(self, args: Dict) -> Dict:
        if not self.session_id:
            raise ValueError(f"[{self.name}] Session ID not initialized. Cannot call MCP tools.")
        if "session_id" not in args:
            args["session_id"] = self.session_id
        return args

    async def init(self):
        # init MCP sessions
        result = await self.mcp_client.call_tool("init_debug_session", {
            "dapp": self.dapp,
        })

        tx2init = result
        if isinstance(result, dict) and "session_id" in result:
            self.session_id = result["session_id"]
            tx2init = result.get("tx2init", {})
            print(f"[{self.name}] Session initialized with ID: {self.session_id}")

        return tx2init

    async def handle(self, global_fault_understanding: dict, task_tree: str, force_terminate: bool = False) -> Dict:
        insights = []
        switch_data = {}

        self._debug_turn_left = TX_DEBUG_TURN
        self._idle_turns = 0

        if force_terminate:
            self.logger.info(f"[{self.name}] Upon receiving the 'Force Terminate' signal, skip the regular analysis and directly generate the patch report.")
            return await self._force_ready_for_patch(
                global_fault_understanding=global_fault_understanding,
                task_tree=task_tree,
                fallback_reason="GUIDE_TURN_EXHAUSTED"
            )

        # analyze trace
        while self._debug_turn_left > 0:
            current_tree = await self.get_current_trace()
            user_prompt = self._judge_too_long_trace(global_fault_understanding, current_tree, task_tree)
            tool_calls = await self.query_with_tools(user_prompt)
            self.is_init = False

            if not tool_calls:
                return {
                    "reason": "ERROR",
                    "data": {}
                }

            if isinstance(tool_calls, str):
                return {
                    "reason": "ERROR",
                    "data": tool_calls
                }

            pending_return_result = None
            # parse tool calls
            for tool_call in tool_calls:
                func_name = tool_call.function.name
                func_args_str = tool_call.function.arguments
                call_id = tool_call.id

                func_args = self._safe_parse_tool_args(func_args_str)
                if func_args is None:
                    error_msg = f"Error: Arguments for tool '{func_name}' could not be parsed as JSON. Please verify the JSON format (escape newlines). Raw: {func_args_str[:200]}..."
                    self.logger.warning(f"[{self.name}] JSON parsing failed. An error has been returned to the LLM: {func_name}")

                    self.memory.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": func_name,
                        "content": error_msg
                    })
                    continue

                if func_name == "ready_for_patch":
                    fault_report = func_args.get("fault_report", "")
                    fix_report = func_args.get("fix_report", "")
                    self.memory.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": func_name,
                        "content": "The patch verification is in progress. Please wait for the patch verification result to be returned."
                    })
                    if pending_return_result is None:
                        final_trace = await self.get_current_trace()
                        self.logger.log_patch_report(fault_report, final_trace, fix_report)
                        pending_return_result = {
                            "reason": "READY_FOR_PATCH",
                            "data": [fault_report, final_trace, fix_report]
                        }
                    continue

                if func_name == "switch_transaction":
                    target_tx_hash = func_args.get("target_tx_hash", "")
                    if target_tx_hash != "" and str(target_tx_hash).lower() in self.tx_list:
                        insight = func_args.get("insight", "")
                        code_patch = func_args.get("code_patch", "")
                        switch_data = {
                            "current_tx": self.current_tx,
                            "insight": insight,
                            "code_patch": code_patch,
                            "switch_to": target_tx_hash
                        }
                        if func_name not in ["init_debug_session", "ready_for_patch", "update_understanding"]:
                            func_args = self._inject_session(func_args)

                        tool_result = await self.mcp_client.call_tool(func_name, func_args)
                        self.current_tx = target_tx_hash
                        self.logger.log_tool_result(func_name, tool_result)
                        self.memory.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": func_name,
                            "content": str(tool_result)
                        })
                        continue

                if func_name == "update_understanding":
                    insights.append(func_args.get("insight", ""))

                if func_name not in ["init_debug_session", "ready_for_patch", "update_understanding"]:
                    func_args = self._inject_session(func_args)

                tool_result = await self.mcp_client.call_tool(func_name, func_args)
                self.logger.log_tool_result(func_name, tool_result)

                self.memory.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": func_name,
                    "content": str(tool_result)
                })

            if pending_return_result:
                return pending_return_result

            has_progress = (len(switch_data) > 0) or (len(insights) > 0)

            if has_progress:
                self._idle_turns = 0
            else:
                self._idle_turns += 1

            self._debug_turn_left -= 1

            if len(switch_data) > 0:
                previous_transactions_summary = self._extract_previous_transactions_summary(global_fault_understanding)
                self.switch_transaction_context(switch_data, previous_transactions_summary)
                return {"reason": "CONTINUE_DEBUGGING", "switch_data": switch_data}
            if len(insights) > 0:
                return {"reason": "CONTINUE_DEBUGGING", "data": insights}

            if self._idle_turns >= TX_DEBUG_MAX_IDLE_TURNS:
                return await self._force_ready_for_patch(
                    global_fault_understanding=global_fault_understanding,
                    task_tree=task_tree,
                    fallback_reason="TIMEOUT_IDLE_LOOP"
                )

        return await self._force_ready_for_patch(
            global_fault_understanding=global_fault_understanding,
            task_tree=task_tree,
            fallback_reason="MAX_TURNS_REACHED"
        )

    def _judge_too_long_trace(self, global_fault_understanding: dict,
                              trace_str: str, task_tree: str, safety_buffer: int = 1000) -> str:
        """
        Construct user prompt words and check if the Trace is too large. If it is too large, prioritize trimming the Trace and injecting a warning.
        Strategy:
        1. Calculate the current Context budget (total limit - reserved output - safety margin - current historical record).
        2. Calculate the token cost of the static part of the Prompt except for Trace.
        3. Allocate all the remaining space to Trace.
        4. If Trace exceeds the budget, perform truncation at the tiktoken level and add a Warning.
        """
        current_history_tokens = self.tm.calculate_total_tokens(self.memory)
        max_output = MODEL_MAX_OUTPUT_TOKENS.get(self.model, 8192)

        global_fault_json = json.dumps(global_fault_understanding, ensure_ascii=False, indent=4)
        dummy_prompt = TRACE_DEBUG_FORCE_COLLAPSE_UP.format(
            global_fault_understanding=global_fault_json,
            trace="",
            task_tree=task_tree
        )
        static_part_tokens = self.tm.count_tokens(dummy_prompt)
        total_limit = self.tm.context_limit
        available_for_trace = total_limit - max_output - safety_buffer - current_history_tokens - static_part_tokens

        if available_for_trace < 25000:
            available_for_trace = 25000

        trace_tokens = self.tm.count_tokens(trace_str)
        print(
            f"trace: {trace_tokens} | available: {available_for_trace} | ratio: {str(trace_tokens / available_for_trace)}")

        if trace_tokens <= available_for_trace:
            user_prompt = TRACE_DEBUG_UP_INIT if self.is_init else TRACE_DEBUG_UP_LATER
            return user_prompt.format(
                global_fault_understanding=global_fault_json,
                trace=trace_str,
                task_tree=task_tree
            )
        else:
            encoded_trace = self.tm.encoder.encode(trace_str)
            pruned_encoded = encoded_trace[:available_for_trace]
            pruned_trace_str = self.tm.encoder.decode(pruned_encoded)

            pruned_trace_str += "\n... [⛔ Trace Data Truncated due to Context Limit ⛔] ..."

            final_prompt = TRACE_DEBUG_FORCE_COLLAPSE_UP.format(
                global_fault_understanding=global_fault_json,
                trace=pruned_trace_str,
                task_tree=task_tree
            )
            return final_prompt

    async def init_transactions(self, txs_need_analyze):
        if not txs_need_analyze or len(txs_need_analyze) == 0:
            raise ValueError(f"[{self.name}] Error: txs_need_analyze is empty, cannot initialize transactions.")
        self.current_tx = txs_need_analyze[0]
        await self.mcp_client.call_tool("set_current_tx",
                                        self._inject_session({"tx_hash": txs_need_analyze[0]}))

    def init_prompt(self, processed_data):
        txs_need_analyze = processed_data["transactions_need_analyze"]
        is_multi = True if len(txs_need_analyze) > 1 else False
        new_trace_sp = TRACE_DEBUG_SP.replace("{multi_attack_part}", MULTI_ATTACK_PART if is_multi else "")
        self.init_system_prompt(new_trace_sp)

    def switch_transaction_context(self, switch_data, previous_transactions_summary=None):
        """
        When switching transactions, retain the key analysis history and integrate the analysis results of previous transactions.
        Args:
        switch_data: A dictionary containing the information about the current transaction switch
        previous_transactions_summary: The summary information of all analyzed transactions previously
        """
        system_prompt = self.memory[0]

        switch_content = TRACE_SWITCH_TX_UP.format(
            raw_tx=switch_data["current_tx"],
            insight=switch_data["insight"],
            code_patch=switch_data["code_patch"],
            switch_to=switch_data["switch_to"],
            previous_transactions_summary=previous_transactions_summary or "无"
        )

        summary_msg = {
            "role": "user",
            "content": switch_content
        }

        preserved_memory = [system_prompt]

        recent_insights = []
        for msg in reversed(self.memory[-10:]):
            if isinstance(msg, dict) and msg.get("role") == "tool":
                if msg.get("name") in ["update_understanding", "get_function_source", "get_mixed_execution_flow"]:
                    content = str(msg.get("content", ""))
                    recent_insights.append({
                        "role": "assistant",
                        "content": f"In the previous trading analysis, it was discovered that：{content}"
                    })
                    if len(recent_insights) >= 5:
                        break

        self.memory = preserved_memory + recent_insights + [summary_msg]

    async def close(self):
        if self.session_id:
            await self.mcp_client.call_tool("close_session", {"session_id": self.session_id})
            self.session_id = None

    async def get_current_trace(self):
        return await self.mcp_client.call_tool("get_current_tree", self._inject_session({}))

    @staticmethod
    def _extract_previous_transactions_summary(global_fault_understanding: dict) -> str:
        if not isinstance(global_fault_understanding, dict):
            return "None"

        analyzed_transactions = global_fault_understanding.get("analyzed_transactions", {})
        if not analyzed_transactions:
            return "None"

        summary_parts = ["=" * 80, "【Summary of the key findings from the previous analysis of the transaction】", "=" * 80]

        for tx_hash, tx_data in analyzed_transactions.items():
            if not isinstance(tx_data, dict):
                continue

            summary_parts.append(f"\n### Transaction {tx_hash}...")
            insight = tx_data.get("insight", "")
            if insight:
                summary_parts.append(f"**Key Findings：**\n{insight}")

            code_patch = tx_data.get("code_patch", "")
            if code_patch:
                summary_parts.append(f"**Patch concept：**\n{code_patch}")

            status = tx_data.get("status", "")
            if status:
                summary_parts.append(f"**Analysis status：** {status}")

            summary_parts.append("-" * 80)

        summary_parts.append("\n**Important Note:** When analyzing new transactions, please take into account the key findings from the previously analyzed transactions.")
        summary_parts.append("Evaluate whether the current patching approach can fully protect against all types of attacks.")
        summary_parts.append("And when new vulnerability exploitation methods are discovered in new transactions, promptly update and complete the patching ideas.")
        summary_parts.append("=" * 80)

        return "\n".join(summary_parts)

    async def _force_ready_for_patch(
            self,
            global_fault_understanding: dict,
            task_tree: str,
            fallback_reason: str = "TIMEOUT_IDLE_LOOP"
    ) -> Dict:
        max_force_trials = 2

        for trial in range(max_force_trials):
            current_tree = await self.get_current_trace()
            if trial == 0:
                user_prompt = TRACE_DEBUG_FORCE_PATCH_UP
            else:
                user_prompt = TRACE_DEBUG_FORCE_PATCH_UP + """
[Important Error Correction Notice] In the previous round, you did not invoke the `ready_for_patch` tool as required. In this round, you **must only make one request to invoke the `ready_for_patch` tool**.
"""

            tool_calls = await self.query_with_tools(user_prompt.format(
                global_fault_understanding=json.dumps(global_fault_understanding, ensure_ascii=False),
                trace=current_tree,
                task_tree=task_tree
            ))

            for tool_call in tool_calls:
                func_name = tool_call.function.name
                func_args = json.loads(tool_call.function.arguments)

                if func_name == "ready_for_patch":
                    fault_report = func_args.get("fault_report", "")
                    fix_report = func_args.get("fix_report", "")
                    final_trace = await self.get_current_trace()
                    self.logger.log_patch_report(fault_report, final_trace, fix_report)
                    self.memory.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": func_name,
                        "content": "Logical deadlock / round limit triggered: Forced to enter the patch generation and verification stage"
                    })
                    return {
                        "reason": "READY_FOR_PATCH",
                        "data": [fault_report, final_trace, fix_report]
                    }

        final_trace = await self.get_current_trace()
        return {
            "reason": f"{fallback_reason}_NO_READY_FOR_PATCH",
            "data": [],
            "final_trace": final_trace
        }

    def _safe_parse_tool_args(self, args_str: str) -> dict | None:
        if not args_str:
            return {}

        try:
            return json.loads(args_str)
        except json.JSONDecodeError:
            pass

        cleaned_str = re.sub(r"^```(?:json)?\s*|\s*```$", "", args_str.strip(), flags=re.IGNORECASE | re.DOTALL)

        cleaned_str_escaped = cleaned_str.replace('\n', '\\n')

        try:
            return json.loads(cleaned_str)
        except json.JSONDecodeError:
            try:
                return json.loads(cleaned_str_escaped)
            except Exception:
                pass

        try:
            return ast.literal_eval(cleaned_str)
        except (ValueError, SyntaxError):
            pass

        try:
            py_style_str = cleaned_str.replace("true", "True").replace("false", "False").replace("null", "None")
            return ast.literal_eval(py_style_str)
        except Exception:
            pass

        self.logger.error(f"[{self.name}] ❌ JSON parsing has failed seriously.. Raw content snippet: {args_str[:200]}...")
        return None
