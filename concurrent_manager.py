import subprocess
import time
import os
import sys
import json
from collections import deque

from settings import PROJECT_PATH

MAX_CONCURRENT_WORKERS = 10
TIMEOUT_SECONDS = 3 * 3600
WORKER_SCRIPT = "worker.py"

DATA_DIR = os.path.join(PROJECT_PATH, "dataset/raw")
LOG_DIR = os.path.join(PROJECT_PATH, "experiment_result/logs")
REPORT_DIR = os.path.join(PROJECT_PATH, "experiment_result/pilot_report")


def get_dapp_name_from_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("name", None)
    except Exception as e:
        print(f"⚠️ Warning: Could not read name from {os.path.basename(file_path)}: {e}")
        return None


def run_concurrent_manager():
    os.makedirs(LOG_DIR, exist_ok=True)
    if not os.path.exists(REPORT_DIR):
        os.makedirs(REPORT_DIR, exist_ok=True)
    all_files = [f for f in os.listdir(DATA_DIR) if f.endswith('.json')]
    all_files.sort()

    total_files = len(all_files)

    print(f"🔍 Scanning {total_files} files for resume capability...")

    pending_queue = deque()
    skipped_count = 0

    for idx, filename in enumerate(all_files):
        file_path = os.path.join(DATA_DIR, filename)

        file_name_no_ext = os.path.splitext(filename)[0]

        report_path = os.path.join(REPORT_DIR, filename)

        if os.path.exists(report_path):
            if os.path.getsize(report_path) > 0:
                print(f"⏭️ [Skipped] Case {idx}: {filename} (Report exists)")
                skipped_count += 1
                continue

        dapp_name = get_dapp_name_from_file(file_path) or file_name_no_ext

        pending_queue.append((idx, filename, dapp_name))

    print(f"\n🔥 Starting Concurrent Manager")
    print(f"🔥 Max Workers: {MAX_CONCURRENT_WORKERS} | Timeout: {TIMEOUT_SECONDS}s")
    print(f"🔥 Total Files: {total_files} | Skipped: {skipped_count} | To Process: {len(pending_queue)}\n")

    # { process_object: { 'index': int, 'name': str, 'start_time': float, 'log_file': file_handle } }
    running_pool = {}

    finished_count = 0
    total_tasks = len(pending_queue)

    try:
        while len(pending_queue) > 0 or len(running_pool) > 0:
            current_time = time.time()
            ended_processes = []

            for process, info in running_pool.items():
                idx = info['index']
                name = info['name']

                ret_code = process.poll()

                if ret_code is not None:
                    print(f"✅ [Case {idx} - {name}] Finished (Exit Code: {ret_code})")
                    info['log_file'].close()
                    ended_processes.append(process)
                    finished_count += 1
                    continue

                runtime = current_time - info['start_time']
                if runtime > TIMEOUT_SECONDS:
                    print(f"⏰ [Case {idx} - {name}] TIMEOUT ({runtime:.1f}s). Killing...")
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        print(f"💀 [Case {idx}] Forced Kill.")
                        process.kill()

                    info['log_file'].close()
                    ended_processes.append(process)
                    finished_count += 1

            for p in ended_processes:
                del running_pool[p]

            while len(running_pool) < MAX_CONCURRENT_WORKERS and len(pending_queue) > 0:
                next_idx, next_filename, next_dapp_name = pending_queue.popleft()

                file_path = os.path.join(DATA_DIR, next_filename)

                log_path = os.path.join(LOG_DIR, f"case_{next_idx}_{next_dapp_name}.log")
                log_file = open(log_path, "w", encoding='utf-8')

                print(
                    f"▶️ [Case {next_idx}] Starting {next_dapp_name}... (Pool: {len(running_pool) + 1}/{MAX_CONCURRENT_WORKERS})")

                proc = subprocess.Popen(
                    [sys.executable, WORKER_SCRIPT, "--case_index", str(next_idx), "--file_path", file_path],
                    stdout=log_file,
                    stderr=subprocess.STDOUT
                )

                running_pool[proc] = {
                    'index': next_idx,
                    'name': next_dapp_name,
                    'start_time': time.time(),
                    'log_file': log_file
                }

            time.sleep(2)
            if int(current_time) % 60 == 0:
                print(f"📊 Progress: {finished_count}/{total_tasks} New Tasks Finished | {len(running_pool)} Running")

    except KeyboardInterrupt:
        print("\n🛑 Manager Interrupted! Killing all workers...")
        for p in running_pool:
            p.kill()
        sys.exit(1)

    print("\n🎉 All tasks completed!")


if __name__ == '__main__':
    run_concurrent_manager()
