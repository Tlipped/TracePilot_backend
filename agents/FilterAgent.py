import ast
import json
import re
from typing import Dict, Any, Union, List

from prompt.filter_prompt import FILTER_SP, FILTER_UP

from agents.AgentBase import AgentBase


class FilterAgent(AgentBase):
    def __init__(self, dapp_name, name="Transaction Filter"):
        super(FilterAgent, self).__init__(name, FILTER_SP, max_turns=1, unique_id=dapp_name)

    async def handle(self, processed_data: Dict[str, Any]) -> List[str]:
        trace_tree_raw = processed_data.get("trace_tree", {})
        candidate_txs = processed_data.get("attack_transactions", [])

        if not candidate_txs:
            print(f"[{self.name}] No attack transactions to filter.")
            return []

        trace_tree = {}
        if isinstance(trace_tree_raw, str):
            try:
                trace_tree = json.loads(trace_tree_raw)
            except json.JSONDecodeError:
                print(f"[{self.name}] Failed to parse trace_tree string.")
        elif isinstance(trace_tree_raw, dict):
            trace_tree = trace_tree_raw

        attack_traces_map = {}
        trace_str_parts = []

        for attack_tx in candidate_txs:
            if attack_tx in trace_tree:
                trace_content = trace_tree[attack_tx]
                attack_traces_map[attack_tx] = trace_content
                trace_str_parts.append(f"\n{attack_tx}: {trace_content}")

        if not trace_str_parts:
            print(f"[{self.name}] No traces found for candidate transactions. Returning all candidates.")
            return candidate_txs

        trace_str = "".join(trace_str_parts)

        try:
            result = await self.query(FILTER_UP.format(traces=trace_str), _format="str")
        except Exception as e:
            print(f"[{self.name}] LLM query failed: {e}")
            return candidate_txs

        parsed_txs = self._parse_llm_output(result)

        valid_txs = [tx for tx in parsed_txs if tx in candidate_txs]

        if not valid_txs and parsed_txs:
            print(f"[{self.name}] LLM returned transactions verify failed (hallucination?). Output: {parsed_txs}")
            return []

        if parsed_txs is None:
            print(f"[{self.name}] Parsing failed, applying Fail-Open strategy.")
            return candidate_txs

        return valid_txs

    def _parse_llm_output(self, llm_output: str) -> Union[List[str], None]:
        cleaned = re.sub(r"```[a-zA-Z]*", "", llm_output).replace("```", "").strip()

        result_list = None

        try:
            val = ast.literal_eval(cleaned)
            if isinstance(val, list):
                result_list = val
        except (ValueError, SyntaxError):
            pass

        if result_list is None:
            try:
                val = json.loads(cleaned)
                if isinstance(val, list):
                    result_list = val
            except json.JSONDecodeError:
                pass

        if result_list is None:
            matches = re.findall(r"['\"](0x[a-fA-F0-9]+)['\"]", cleaned)
            if matches:
                result_list = matches

        if result_list is not None:
            return [str(item) for item in result_list if isinstance(item, (str, bytes))]

        print(f"[{self.name}] Failed to parse output: {llm_output}")
        return None
