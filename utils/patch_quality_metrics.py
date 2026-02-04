import json
import os
import time
from typing import Dict

from settings import PROJECT_PATH


class PatchQualityMetrics:
    def __init__(self, save_dir=os.path.join(PROJECT_PATH, "experiment_result", "metrics_logs")):
        self.save_dir = save_dir
        os.makedirs(save_dir, exist_ok=True)
        self.case_records = {}

    def _get_case_path(self, case_name):
        return os.path.join(self.save_dir, f"{case_name}.json")

    def _flush(self, case_name):
        """
        Atomic write using a temporary file and os.replace to ensure data integrity
        even if the process is interrupted or accessed concurrently.
        """
        if case_name not in self.case_records:
            return

        data = self.case_records[case_name]
        path = self._get_case_path(case_name)
        temp_path = path + ".tmp"

        try:
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            os.replace(temp_path, path)
        except Exception as e:
            print(f"[Metrics] Save failed for {case_name}: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def start_case(self, case_name):
        """Initialize or resume a tracking record."""
        path = self._get_case_path(case_name)

        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    self.case_records[case_name] = json.load(f)
            except Exception:
                self._init_new_record(case_name)
        else:
            self._init_new_record(case_name)

        self._flush(case_name)

    def _init_new_record(self, case_name):
        self.case_records[case_name] = {
            "name": case_name,
            "status": "RUNNING",
            "start_time": time.time(),
            "end_time": 0,

            # --- Metrics Flags ---
            # ER: Did it compile/run at least once?
            "compilable_at_least_once": False,
            # FSR: Was the final result VERIFIED?
            "fix_success": False,
            # LFC/PFC: Was the root cause localization correct?
            "localization_correct": False,

            # --- Counters ---
            "total_turns": 0,

            # --- Snapshots ---
            "latest_hypothesis": {},
            "final_verdict": "UNKNOWN",
            "judge_reason": ""
        }

    # =========================================================
    # Runtime Updates
    # =========================================================

    def record_analysis_hypothesis(self, case_name, hypothesis_data: Dict):
        """Saves the latest hypothesis (fault/fix report)."""
        if case_name not in self.case_records: return
        self.case_records[case_name]["latest_hypothesis"] = {
            "fault_report": hypothesis_data.get("fault_report", ""),
            "fix_report": hypothesis_data.get("fix_report", ""),
            "timestamp": time.time()
        }
        self._flush(case_name)

    def record_compile_status(self, case_name, success: bool):
        """
        Updates executability (ER).
        If success is True once, the case is permanently marked as executable.
        """
        if case_name not in self.case_records: return

        if success:
            self.case_records[case_name]["compilable_at_least_once"] = True

        self._flush(case_name)

    def record_fix_turns(self, case_name, turns):
        """Records total patch iterations used."""
        if case_name not in self.case_records: return
        self.case_records[case_name]["total_turns"] = turns
        self._flush(case_name)

    def record_judge_result(self, case_name, verdict, reason=""):
        """
        Updates the metrics based on the Judge's Verdict.

        Logic Mapping for LFC/PFC:
        - VERIFIED: Fix worked. Localization must be Correct.
        - INEFFECTIVE_PATCH: Fix failed. Localization Ambiguous (assume Incorrect for strict PFC, or Unknown).
          *Strict Policy*: We treat INEFFECTIVE as Incorrect Localization here unless verified later.
        - WRONG_ROOT_CAUSE: Fix failed. Localization Incorrect.
        """
        if case_name not in self.case_records: return

        self.case_records[case_name]["final_verdict"] = verdict
        self.case_records[case_name]["judge_reason"] = reason

        if verdict == "VERIFIED":
            self.case_records[case_name]["fix_success"] = True
            self.case_records[case_name]["localization_correct"] = True

        elif verdict in ["WRONG_ROOT_CAUSE", "INEFFECTIVE_PATCH"]:
            self.case_records[case_name]["fix_success"] = False
            self.case_records[case_name]["localization_correct"] = False

        else:
            # UNKNOWN, TIMEOUT, ERROR
            self.case_records[case_name]["fix_success"] = False
            self.case_records[case_name]["localization_correct"] = False

        self._flush(case_name)

    def finalize_case(self, case_name, final_status_label):
        """Marks the case as finished and sets end timestamp."""
        if case_name not in self.case_records: return

        self.case_records[case_name]["status"] = final_status_label
        self.case_records[case_name]["end_time"] = time.time()

        self._flush(case_name)
