import json
import os
import time
import traceback
import asyncio
from typing import Any, Dict, List, Optional, Callable

from settings import LLM_NAME, CACHE_DIR
from utils.agent_helpers import AgentLogger, TokenManager, ResponseParser
from utils.llm_client import LLMClient
from app.models import LogMessage

class AgentBase:
    def __init__(self, name: str, role_prompt: str, unique_id: str = "default", need_tool=False, max_turns=1,log_callback: Optional[Callable[[LogMessage], None]] = None):
        self.name = name
        self.model = LLM_NAME
        self.max_turns = max_turns
        self.need_tool = need_tool

        self.logger = AgentLogger(name, unique_id, log_callback=log_callback)
        self.tm = TokenManager(self.model)
        self.parser = ResponseParser()

        self.llm_client = LLMClient()

        self.token = 0
        self.times = []
        self.memory: List[Dict[str, Any]] = [{"role": "system", "content": role_prompt}]
        self.mcp_client = None
        self.safety_margin = 0.8
        self.target_margin = 0.6
        self.cancellation_checker: Callable[[], bool] = lambda: False

    def set_cancellation_checker(self, cancellation_checker: Optional[Callable[[], bool]]):
        self.cancellation_checker = cancellation_checker or (lambda: False)

    def _check_cancelled(self):
        if self.cancellation_checker():
            raise asyncio.CancelledError("Task was cancelled")

    def init_system_prompt(self, role_prompt):
        self.memory = [{"role": "system", "content": role_prompt}]

    def _get_memory_snapshot_str(self, history: list) -> str:
        lines = [f"{'Index':<7} | {'Role':<12} | {'Tokens':<8} | {'Content Snippet'}", "-" * 80]
        for i, msg in enumerate(history):
            role = self.tm.get_role(msg)
            content = self.tm.get_content(msg)
            content_str = str(content) if content else ""

            tool_calls = None
            if isinstance(msg, dict):
                tool_calls = msg.get('tool_calls')
            elif hasattr(msg, 'tool_calls'):
                tool_calls = msg.tool_calls
            if not content and tool_calls:
                content_str = f"[Tool Calls: {len(tool_calls)} items]"
            tokens = self.tm.count_tokens(content_str)
            snippet = (content_str.replace('\n', ' ')[:60] + "...") if len(content_str) > 60 else content_str
            lines.append(f"[{i:<5}] | {role.upper():<12} | {tokens:<8} | {snippet}")
        return "\n".join(lines)

    async def query(self, user_message: str, temperature=1.0, _format='json'):
        self._check_cancelled()
        self.logger.display_start("Query", user_message)
        result = await self._send_message(user_message, temperature)
        self._check_cancelled()
        if _format == 'str':
            return result

        try:
            return self.parser.process_response(result, _format)
        except Exception:
            try:
                return self.parser.process_response(result, _format)
            except Exception:
                return result

    async def _send_message(self, user_message: str, temperature=1.0) -> str:
        try:
            self._check_cancelled()
            truncated_user_msg = self.tm.truncate_prompt(user_message)
            self.memory.append({"role": "user", "content": truncated_user_msg})

            start_time = time.time()
            chat_completion = await self.llm_client.create_chat_completion(
                messages=self.tm.truncate_history(self.memory, self.max_turns),
                model=self.model,
                temperature=temperature
            )
            elapsed_time = time.time() - start_time
            self._check_cancelled()

            reply = chat_completion.choices[0].message.content
            usage_token = int(chat_completion.usage.total_tokens)

            self.times.append(elapsed_time)
            self.token += usage_token
            self.memory.append({"role": "assistant", "content": str(reply)})

            snapshot = self._get_memory_snapshot_str(self.tm.truncate_history(self.memory[:-1], self.max_turns))
            self.logger.log_full_interaction("Query", snapshot, truncated_user_msg, reply)
            self.logger.display_result(reply, {"time": elapsed_time, "token": usage_token})

            return reply
        except Exception as e:
            self._handle_error(e)

    async def query_with_tools(self, user_message: str, temperature=1.0):
        self._check_cancelled()
        self.logger.display_start("QueryWithTools", user_message)
        ignorable_tools = ["get_current_tree", "expand_node", "collapse_node",
                           "get_patch_items", "set_current_tx", "set_attack_tx_list", "close_session"]
        try:
            self._check_cancelled()
            truncated_user_msg = self.tm.truncate_prompt(user_message)
            self.memory.append({"role": "user", "content": truncated_user_msg})
            clean_memory = self.tm.filter_redundant_messages(self.memory, ignorable_tools)
            compressed_memory = self.tm.compress_messages(clean_memory, threshold=4000)
            final_messages = self.tm.truncate_history_by_token(compressed_memory)

            tools = await self._get_openai_tools() if self.mcp_client else None
            self._check_cancelled()
            start_time = time.time()
            response = await self.llm_client.create_chat_completion(
                model=self.model,
                messages=final_messages,
                tools=tools,
                tool_choice="auto",
                temperature=temperature
            )
            elapsed_time = time.time() - start_time
            self._check_cancelled()

            message = response.choices[0].message
            self.memory.append(message.model_dump())
            usage_token = int(response.usage.total_tokens)
            self.token += usage_token
            self.times.append(elapsed_time)

            snapshot = self._get_memory_snapshot_str(final_messages)
            self.logger.log_full_interaction("QueryWithTools", snapshot, truncated_user_msg, message.content,
                                             message.tool_calls)
            self.logger.display_tool_decision(message.tool_calls)

            return message.tool_calls
        except Exception as e:
            self._handle_error(e)

    def _handle_error(self, e):
        error_str = str(e)
        error_type = type(e).__name__
        print(f"\n❌ API Error: {type(e).__name__}\ndetail: {error_str}")
        self.logger.file_logger.error(traceback.format_exc())
        self.memory.append({"role": "assistant", "content": ''})
        raise RuntimeError(f"Agent '{self.name}' API Error: {error_type} - {error_str}") from e

    async def _get_openai_tools(self):
        if not self.mcp_client:
            return None

        mcp_tools_result = await self.mcp_client.list_tools()
        tools = []
        hidden_tools = [
            "get_current_tree",
            "init_debug_session",
            "set_current_tx",
            "set_attack_tx_list",
            "get_patch_item",
            "close_session"
        ]

        for tool in mcp_tools_result.tools:
            if tool.name in hidden_tools:
                continue
            schema = tool.inputSchema.copy()
            if "properties" in schema and "session_id" in schema["properties"]:
                del schema["properties"]["session_id"]
            if "required" in schema and isinstance(schema["required"], list):
                if "session_id" in schema["required"]:
                    schema["required"] = [p for p in schema["required"] if p != "session_id"]

            tools.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": schema
                }
            })

        return tools

    def load_summary_from_cache(self, cache_path: str):
        with open(cache_path, 'r', encoding="utf-8") as f:
            cache = json.load(f)
            summary = cache.get("summary", "")
            self.token += int(cache.get("token", 0))
            time_val = cache.get("time", "")
            if time_val:
                if isinstance(time_val, str):
                    loaded_times = [float(t) for t in time_val.split(',') if t.strip()]
                    self.times.extend(loaded_times)
                else:
                    self.times.append(float(time_val))
        return summary

    def write_summary_to_cache(self, cache_path: str, file_name: str, summary: str):
        path = os.path.join(CACHE_DIR, cache_path)
        if not os.path.exists(path):
            os.makedirs(path)
        path = os.path.join(path, '%s.json' % file_name)
        with open(path, 'w', encoding="utf-8") as f:
            json.dump({'summary': summary, 'token': str(self.token), 'time': ','.join([str(t) for t in self.times])},
                      f, ensure_ascii=False, indent=4)

    def get_total_time(self):
        return sum(self.times)
