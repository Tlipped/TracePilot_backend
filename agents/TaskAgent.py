import json
from typing import Dict

from agents.AgentBase import AgentBase
from prompt.task_prompt import INIT_TASK_TREE, TASK_SP, TASK_UP, TASK_END_UP


class TaskAgent(AgentBase):
    def __init__(self, mcp_client, dapp_name, session_id, name="Task Organizer"):
        super(TaskAgent, self).__init__(name, TASK_SP, max_turns=2, unique_id=dapp_name)
        self.status = "CONTINUE_DEBUGGING"
        self.thought_process = ""
        self.task_tree = INIT_TASK_TREE
        self.suspicious_functions = []
        self.mcp_client = mcp_client
        self.last_end_result = ""
        self.session_id = session_id

    async def handle(self, global_fault_understanding, patch_feedback):
        trace = await self.mcp_client.call_tool("get_current_tree", self._inject_session({}))
        result = await self.query(TASK_UP.format(
            global_fault_understanding=global_fault_understanding,
            trace=trace,
            patch_feedback=patch_feedback,
            task_tree=self.task_tree
        ), _format="json")
        self.task_tree = result.get("task_tree", INIT_TASK_TREE)
        self.suspicious_functions = result.get("suspicious_functions", [])
        self.status = result.get("status", "CONTINUE_DEBUGGING")
        self.thought_process = result.get("thought_process", "")
        return result

    async def end_analyze(self, end_result):
        result = await self.query(TASK_END_UP.format(
            final_report=json.dumps(end_result, ensure_ascii=False)
        ))
        self.last_end_result = result

    def _inject_session(self, args: Dict) -> Dict:
        if not self.session_id:
            raise ValueError(f"[{self.name}] Session ID not initialized. Cannot call MCP tools.")
        if "session_id" not in args:
            args["session_id"] = self.session_id
        return args
