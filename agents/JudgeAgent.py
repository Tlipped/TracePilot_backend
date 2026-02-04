from typing import Dict

from agents.AgentBase import AgentBase
from prompt.tx_judge_prompt import PATCH_JUDGE_UP, PATCH_JUDGE_SP


class JudgeAgent(AgentBase):
    def __init__(self, dapp_name, name="Transaction Judge", metrics_collector=None):
        super(JudgeAgent, self).__init__(name, PATCH_JUDGE_SP, unique_id=dapp_name, max_turns=2)
        self.metrics_collector = metrics_collector
        self.unique_id = dapp_name

    async def handle(self, judge_data: Dict) -> Dict:
        result = await self.query(PATCH_JUDGE_UP.format(
            current_hypothesis=judge_data["current_hypothesis"],
            replay_logs=judge_data["replay_logs"],
            patches=judge_data["patches"],
            real_balance_change=judge_data["real_balance_change"]
        ), _format="json")

        if self.metrics_collector and isinstance(result, dict):
            verdict = result.get("verdict")
            if verdict:
                feedback = str(result.get("reason", "")) + " " + str(result.get("feedback_to_agent", ""))
                self.metrics_collector.record_judge_result(
                    case_name=self.unique_id,
                    verdict=verdict,
                    reason=feedback
                )
        
        return result
