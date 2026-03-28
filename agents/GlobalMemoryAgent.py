import copy
import json
from typing import Dict, Any, List

from agents.AgentBase import AgentBase
from prompt.global_memory_prompt import GLOBAL_MEMORY_SP, GLOBAL_MEMORY_UP, GLOBAL_MEMORY_FINAL_UP, MULTI_ATTACK_PART


class GlobalMemoryAgent(AgentBase):
    def __init__(self, is_multi: bool, dapp_name: str, name: str = "GlobalMemory Administrator",log_callback=None):
        """
        init global memory
        :param is_multi: if multi transaction
        :param dapp_name: DApp name
        :param name: Agent name
        """
        global_memory_sp = GLOBAL_MEMORY_SP.replace("{multi_attack_part}", MULTI_ATTACK_PART if is_multi else "")

        super(GlobalMemoryAgent, self).__init__(name, global_memory_sp, max_turns=2, unique_id=dapp_name,log_callback=log_callback)

        self.global_memory: Dict[str, Any] = {}

        self._memory_history: List[Dict[str, Any]] = []
        self._max_history_len = 10

        self._protected_keys = {
            "transaction_abnormal_analysis",
            "transaction_roles",
            "transactions_need_analyze",
            "auxiliary_transactions",
            "analyzed_transactions",
            "current_transaction"
        }

    def init(self, processed_data: Dict[str, Any]):
        self.global_memory = {
            "transaction_abnormal_analysis": processed_data.get("bug_summary", ""),
            "transaction_roles": processed_data.get("transaction_roles", ""),
            "transactions_need_analyze": processed_data.get("transactions_need_analyze", []),
            "auxiliary_transactions": processed_data.get("auxiliary_transactions", []),
            "analyzed_transactions": {},
            "current_transaction": None
        }
        self._create_snapshot("Initial State")
        print(f"[{self.name}] Memory initialized.")

    def init_transactions(self, txs_need_analyze: List[str]):
        if not txs_need_analyze:
            print(f"[{self.name}] Warning: No transactions to analyze.")
            return

        if len(txs_need_analyze) == 0:
            raise ValueError(f"[{self.name}] Error: txs_need_analyze is empty, cannot initialize transactions.")

        self._create_snapshot("Init Transactions")
        self.global_memory["current_transaction"] = txs_need_analyze[0]
        self.global_memory["transactions_need_analyze"] = txs_need_analyze
        print(f"[{self.name}] Transaction list initialized. Current: {txs_need_analyze[0]}")

    async def handle(self, dapp_data: Dict) -> str:
        pass

    async def update(self, agent_name: str, new_data: Any):
        try:
            llm_response = await self.query(GLOBAL_MEMORY_UP.format(
                agent=agent_name,
                new_data=new_data,
                current_understanding=self._serialize_memory_for_prompt()
            ), _format="json")
        except Exception as e:
            print(f"[{self.name}] Critical Error: LLM query failed during update. {e}")
            return

        if not isinstance(llm_response, dict):
            print(f"[{self.name}] Error: LLM returned non-dict format: {type(llm_response)}")
            return

        self._create_snapshot(f"Update by {agent_name}")

        print(f"[{self.name}] Merging updates from {agent_name}...")
        try:
            self._deep_update(self.global_memory, llm_response)
        except Exception as e:
            print(f"[{self.name}] Error during deep merge: {e}. Rolling back...")
            self._rollback()
            return

        if not self._validate_integrity():
            print(f"[{self.name}] Validation failed after update. Rolling back...")
            self._rollback()
        else:
            print(f"[{self.name}] Memory updated successfully.")

    async def switch_transaction(self, agent_name: str, switch_data: Dict):
        await self.update(agent_name, switch_data)

        self._create_snapshot(f"Switch Transaction to {switch_data.get('switch_to')}")

        current_tx = switch_data.get("current_tx")
        insight = switch_data.get("insight")
        code_patch = switch_data.get("code_patch")
        next_tx = switch_data.get("switch_to")

        if "analyzed_transactions" not in self.global_memory:
            self.global_memory["analyzed_transactions"] = {}

        if current_tx:
            if current_tx not in self.global_memory["analyzed_transactions"]:
                self.global_memory["analyzed_transactions"][current_tx] = {}

            tx_record = self.global_memory["analyzed_transactions"][current_tx]
            tx_record["insight"] = insight
            tx_record["code_patch"] = code_patch
            tx_record["status"] = "Analyzed"

        if next_tx:
            self.global_memory["current_transaction"] = next_tx
            print(f"[{self.name}] Switched focus to transaction: {next_tx}")
        else:
            print(f"[{self.name}] Warning: No 'switch_to' transaction provided.")

    async def get_final_result(self, judge_result, current_hypothesis, replay_logs, patches):
        verification_result = (f"Patch Validation Details: {replay_logs}\n "
                               f"Transaction Judge Output: {judge_result}")

        fault_report = current_hypothesis.get("fault_report", "N/A")
        final_trace = current_hypothesis.get("final_trace", "N/A")
        fix_report = current_hypothesis.get("fix_report", "N/A")

        final_report = await self.query(GLOBAL_MEMORY_FINAL_UP.format(
            verification_result=verification_result,
            current_understanding=self._serialize_memory_for_prompt(),
            fault_report=fault_report,
            final_trace=final_trace,
            fix_report=fix_report
        ), _format="str")

        final_output = (
            f"{final_report}\n\n"
            f"## Applied Patches (Verified)\n"
            f"```solidity\n"
            f"{patches}\n"
            f"```"
        )
        return final_output

    def _deep_update(self, target: Dict, source: Dict):
        for key, value in source.items():
            if isinstance(value, dict) and key in target and isinstance(target[key], dict):
                self._deep_update(target[key], value)
            else:
                target[key] = value

    def _create_snapshot(self, reason: str = ""):
        snapshot = copy.deepcopy(self.global_memory)
        self._memory_history.append({
            "data": snapshot,
            "reason": reason
        })
        if len(self._memory_history) > self._max_history_len:
            self._memory_history.pop(0)

    def _rollback(self):
        if not self._memory_history:
            print(f"[{self.name}] Error: No history to rollback.")
            return

        last_snapshot = self._memory_history.pop()
        if self._memory_history:
            previous_state = self._memory_history[-1]
            self.global_memory = copy.deepcopy(previous_state["data"])
            print(f"[{self.name}] Rolled back memory to state: {previous_state['reason']}")
        else:
            print(f"[{self.name}] Warning: Rolled back to empty state.")
            self.global_memory = {}

    def _validate_integrity(self) -> bool:
        missing_keys = [k for k in self._protected_keys if k not in self.global_memory]
        if missing_keys:
            print(f"[{self.name}] Integrity Violation: Missing protected keys: {missing_keys}")
            return False
        return True

    def _serialize_memory_for_prompt(self) -> str:
        try:
            return json.dumps(self.global_memory, indent=2, ensure_ascii=False)
        except Exception:
            return str(self.global_memory)

    def get_memory_debug_info(self):
        return {
            "current_keys": list(self.global_memory.keys()),
            "history_depth": len(self._memory_history),
            "current_tx": self.global_memory.get("current_transaction")
        }
