from typing import Dict, Optional

from agents.FixAgent import FixAgent
from agents.GlobalMemoryAgent import GlobalMemoryAgent
from agents.JudgeAgent import JudgeAgent
from agents.TaskAgent import TaskAgent
from agents.TraceAgent import TraceAgent
from settings import TOTAL_TURN, GUIDE_TURN, PATCH_TURN
from utils.bucket import AsyncItemBucket
from utils.patch_quality_metrics import PatchQualityMetrics


class DAppAnalyze:
    def __init__(self, processed_data: Dict, transaction_debugger: TraceAgent,
                 net2apikey_bucket: Dict[str, AsyncItemBucket],
                 net2rpc_bucket: Dict[str, AsyncItemBucket],
                 mcp_client,
                 metrics_collector: Optional[PatchQualityMetrics] = None):
        self._net2apikey_bucket = net2apikey_bucket
        self._net2rpc_bucket = net2rpc_bucket
        self.processed_data = processed_data
        self.dapp_name = processed_data["dapp"]["name"]
        self.txs_need_analyze = processed_data["transactions_need_analyze"]
        self.is_multi = True if len(self.txs_need_analyze) > 1 else False
        self.mcp_client = mcp_client
        self.transaction_debugger = transaction_debugger
        self.transaction_debugger.init_prompt(self.processed_data)
        self.global_memory_administrator = GlobalMemoryAgent(self.is_multi, dapp_name=self.dapp_name)
        self.task_organizer = TaskAgent(self.mcp_client, dapp_name=self.dapp_name, session_id=self.transaction_debugger.session_id)
        self.code_patcher = FixAgent(processed_data=processed_data,
                                     apikey_bucket=self._net2apikey_bucket[processed_data["dapp"]["platform"]],
                                     rpc_bucket=self._net2rpc_bucket[processed_data["dapp"]["platform"]],
                                     mcp_client=self.mcp_client,
                                     dapp_name=self.dapp_name,
                                     session_id=self.transaction_debugger.session_id,
                                     metrics_collector=metrics_collector)
        self.transaction_judge = JudgeAgent(dapp_name=self.dapp_name, metrics_collector=metrics_collector)
        self.metrics_collector = metrics_collector

    async def analyze(self) -> [str, Dict]:
        if self.metrics_collector:
            self.metrics_collector.start_case(self.dapp_name)
        
        self.global_memory_administrator.init(self.processed_data)
        verify_feedback = "Initial state: Start analysis."

        if len(self.txs_need_analyze) <= 0:
            if self.metrics_collector:
                self.metrics_collector.finalize_case(self.dapp_name, "NO_ATTACK_TX")
            return "empty attack transaction!", {}

        await self.transaction_debugger.init_transactions(self.txs_need_analyze)
        self.global_memory_administrator.init_transactions(self.txs_need_analyze)

        last_valid_hypothesis = {}
        last_replay_logs = ""
        last_patches = ""

        total_turn = TOTAL_TURN
        while total_turn > 0:
            guide_turn = GUIDE_TURN
            current_hypothesis = {}
            while guide_turn > 0:
                # generate task tree (assume)
                await self.task_organizer.handle(
                    self.global_memory_administrator.global_memory,
                    verify_feedback
                )
                is_force_turn = (guide_turn == 1)
                # transaction debug (analyze)
                debug_result = await self.transaction_debugger.handle(
                    self.global_memory_administrator.global_memory,
                    self.task_organizer.task_tree,
                    force_terminate=is_force_turn
                )
                reason = debug_result.get("reason", "")
                if isinstance(reason, str) and reason == "ERROR":
                    if self.metrics_collector:
                        self.metrics_collector.finalize_case(self.dapp_name, "TRACE_AGENT_ERROR")
                    return f"ERROR_{debug_result.get('data', '')}", {}

                if isinstance(reason, str) and reason.endswith("_NO_READY_FOR_PATCH"):
                    if last_valid_hypothesis:
                        verify_feedback = f"Debugger terminated ({reason}), falling back to last valid hypothesis."
                        break
                    else:
                        final_trace = debug_result.get("final_trace", "")
                        fail_msg = (
                            f"Transaction Debugger terminated without producing a ready_for_patch hypothesis. "
                            f"reason={reason}. "
                            f"Please review the trace and prompts, then consider rerunning the analysis.\n"
                            f"(A snapshot of the final trace is attached in the pipeline output if available.)"
                        )
                        if final_trace:
                            await self.global_memory_administrator.update(
                                self.transaction_debugger.name,
                                {"final_trace_snapshot": final_trace, "termination_reason": reason}
                            )

                        if self.metrics_collector:
                            self.metrics_collector.finalize_case(self.dapp_name, "NO_HYPOTHESIS_GENERATED")
                        return fail_msg, self.get_analyze_items()

                if debug_result["reason"] == "READY_FOR_PATCH" or guide_turn == 1:
                    debug_data = debug_result.get("data", [None, None, None])
                    if len(debug_data) < 3:
                        print("Error: Incomplete report data from debugger")

                    current_hypothesis = {
                        "fault_report": debug_data[0] if len(debug_data) > 0 else "",
                        "final_trace": debug_data[1] if len(debug_data) > 1 else "",
                        "fix_report": debug_data[2] if len(debug_data) > 2 else ""
                    }
                    last_valid_hypothesis = current_hypothesis
                    if self.metrics_collector:
                        self.metrics_collector.record_analysis_hypothesis(self.dapp_name, current_hypothesis)
                    await self.task_organizer.end_analyze(current_hypothesis)
                    await self.global_memory_administrator.update(self.transaction_debugger.name, current_hypothesis)
                    break

                if "data" in debug_result:
                    await self.global_memory_administrator.update(self.transaction_debugger.name, debug_result["data"])
                if "switch_data" in debug_result:
                    await self.global_memory_administrator.switch_transaction(self.transaction_debugger.name,
                                                                              debug_result["switch_data"])
                    self.transaction_debugger.is_init = False
                guide_turn -= 1

            # Patch loop (verify)
            patch_turn = PATCH_TURN
            verification_result = None
            is_finalized = False
            replay_logs, patches = "", ""
            patch_fix_total_count = 0

            while patch_turn > 0:
                patch_execution_success, replay_logs, patches, fix_count = await self.code_patcher.handle(current_hypothesis)
                patch_fix_total_count += fix_count

                if self.metrics_collector:
                    self.metrics_collector.record_fix_turns(self.dapp_name, patch_fix_total_count)

                last_replay_logs = replay_logs
                last_patches = patches

                if patch_execution_success:
                    # transaction judge
                    judge_data = {
                        "real_balance_change": self.processed_data["balance_change"],
                        "replay_logs": replay_logs,
                        "current_hypothesis": current_hypothesis,
                        "patches": patches
                    }
                    judge_result = await self.transaction_judge.handle(judge_data)
                    verdict = judge_result.get("verdict")

                    if verdict == "VERIFIED":  # pass
                        verification_result = judge_result
                        is_finalized = True
                        break
                    elif verdict == "INEFFECTIVE_PATCH":
                        patch_turn -= 1
                        current_hypothesis["fix_feedback"] = (
                            f"Patch failed but Hypothesis seems relevant. Judge's Advice: {judge_result.get('reason')}\n"
                            f"Replay Evidence: {replay_logs}"
                        )
                        print(f"[-] Patch Ineffective. Retrying implementation... ({patch_turn} turns left)")
                        continue
                    elif verdict == "WRONG_ROOT_CAUSE":
                        verify_feedback = (
                            f"Hypothesis REJECTED by Judge. The patch addressed the hypothesis but the attack persisted via a different path. "
                            f"Judge's Analysis: {judge_result.get('reason')}. "
                            f"You must generate a NEW hypothesis analyzing a different vulnerability."
                        )
                        await self.global_memory_administrator.update(self.transaction_judge.name, verify_feedback)
                        print(f"[!] Wrong Root Cause identified. Aborting patch attempts and returning to Debugger.")
                        break
                    elif verdict == "BROKEN_LOGIC":  # too much protect
                        patch_turn -= 1
                        current_hypothesis["fix_feedback"] = (f"Last patch broke original logic:"
                                                              f" {judge_result.get('reason')}")
                        continue
                else:
                    patch_turn -= 1
                    current_hypothesis["fix_feedback"] = f"Patch execution failed: {replay_logs}"

            if is_finalized:
                if self.metrics_collector:
                    self.metrics_collector.finalize_case(self.dapp_name, "VERIFIED")
                return await self.global_memory_administrator.get_final_result(
                    verification_result, current_hypothesis, replay_logs, patches
                ), self.get_analyze_items()

            if patch_turn <= 0:
                verify_feedback = "Patch attempts exhausted without success. Re-evaluating attack trace."

            total_turn -= 1

        if last_valid_hypothesis:
            unverified_result = {
                "verdict": "UNVERIFIED",
                "reason": f"Analysis process terminated after {TOTAL_TURN} turns. "
                          f"This report is based on the latest hypothesis but lacks verification."
            }
            raw_report = await self.global_memory_administrator.get_final_result(
                unverified_result,
                last_valid_hypothesis,
                last_replay_logs or "No replay logs available (Verification Timeout).",
                last_patches or "No finalized patch available."
            )

            warning_header = (
                    "\n" + "!" * 80 + "\n"
                                      "⚠️  WARNING: ANALYSIS INCOMPLETE / UNVERIFIED REPORT  ⚠️\n"
                                      "The system failed to verify the fix within the allowed turns.\n"
                                      "The following report is generated based on the LAST HYPOTHESIS.\n"
                                      "It may contain inaccurate root causes or ineffective patches.\n"
                                      f"Final Status: {verify_feedback}\n"
                    + "!" * 80 + "\n\n"
            )

            return warning_header + raw_report, self.get_analyze_items()

        if self.metrics_collector:
            self.metrics_collector.finalize_case(self.dapp_name, "MAX_TURNS_EXCEEDED")

        return (f"FAILED: Unable to generate any valid hypothesis within {TOTAL_TURN} turns. \n"
                f"Last Feedback: {verify_feedback}"), self.get_analyze_items()

    def get_analyze_items(self):
        return {
            "time": {
                "debugger": self.transaction_debugger.get_total_time(),
                "task": self.task_organizer.get_total_time(),
                "memory": self.global_memory_administrator.get_total_time(),
                "patch": self.code_patcher.get_total_time(),
                "judge": self.transaction_judge.get_total_time()
            },
            "token": {
                "total_token": self.transaction_debugger.token + self.task_organizer.token + self.global_memory_administrator.token + self.code_patcher.token + self.transaction_judge.token,
                "debugger": self.transaction_debugger.token,
                "task": self.task_organizer.token,
                "memory": self.global_memory_administrator.token,
                "patch": self.code_patcher.token,
                "judge": self.transaction_judge.token
            }
        }
