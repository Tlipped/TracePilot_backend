import argparse
import asyncio
import json
import os
import signal
import sys
from main import WorkFlow

current_workflow = None
current_dapp_name = None


async def graceful_shutdown(sig, loop):
    print(f"\n[Worker-{os.getpid()}] ⏳ Received TimeLimit signal. Saving state...")
    if current_workflow and current_dapp_name:
        try:
            if current_workflow.metrics_collector:
                current_workflow.metrics_collector.finalize_case(current_dapp_name, "TIMEOUT_KILLED")
            print(f"[Worker-{os.getpid()}] ✅ Emergency state saved to JSON.")
        except Exception as e:
            print(f"[Worker-{os.getpid()}] ❌ Save failed: {e}")

    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for task in tasks: task.cancel()
    loop.stop()
    sys.exit(0)


async def main():
    global current_workflow, current_dapp_name

    parser = argparse.ArgumentParser()
    parser.add_argument("--file_path", type=str, required=True)
    parser.add_argument("--case_index", type=int, default=0)
    args = parser.parse_args()

    workflow = WorkFlow(semaphore_num=1)
    current_workflow = workflow

    if not os.path.exists(args.file_path):
        print(f"❌ File not found: {args.file_path}")
        return

    try:
        with open(args.file_path, 'r', encoding="utf-8") as f:
            target_dapp = json.load(f)
    except Exception as e:
        print(f"❌ JSON Load Error: {e}")
        return

    current_dapp_name = target_dapp.get("name", "Unknown")

    print(f"[Worker-{os.getpid()}] 🚀 Start processing Case {args.case_index}: {current_dapp_name}")
    print(f"[Worker-{os.getpid()}] 📂 File: {args.file_path}")

    await workflow.process_single_dapp(target_dapp, args.case_index)


if __name__ == '__main__':
    if sys.platform != 'win32':
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(graceful_shutdown(signal.SIGTERM, loop)))
        try:
            loop.run_until_complete(main())
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
    else:
        asyncio.run(main())
