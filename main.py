import asyncio
import json
import os
import time
from typing import Union, List, Tuple
from agents.TraceAgent import TraceAgent
from mcp_tools.mcp_client import MCPClient
from process.analyze import DAppAnalyze
from process.pyg import DAppProcess
from settings import PROJECT_PATH, SCAN_APIKEYS, JSONRPCS, MCP_SERVER_PATH
from utils.bucket import AsyncItemBucket
from utils.patch_quality_metrics import PatchQualityMetrics


class WorkFlow:
    def __init__(self, semaphore_num=1):
        self.root = os.path.join(PROJECT_PATH, 'dataset')
        self.dapp2processed = {}
        self.dapp2report = {}
        self._net2rpc_bucket = {
            net: AsyncItemBucket(items=_rpc_urls, qps=1)
            for net, _rpc_urls in JSONRPCS.items()
        }
        self._net2apikey_bucket = {
            net: AsyncItemBucket(items=_apikeys, qps=1)
            for net, _apikeys in SCAN_APIKEYS.items()
        }

        self.semaphore = asyncio.Semaphore(semaphore_num)
        self.metrics_collector = PatchQualityMetrics()

    @property
    def raw_file_names(self) -> Union[str, List[str], Tuple]:
        path = os.path.join(self.root, 'raw')
        return sorted([os.path.join(path, fn) for fn in os.listdir(path)])

    @property
    def processed_data_names(self) -> Union[str, List[str], Tuple]:
        path = os.path.join(self.root, 'raw')
        return sorted([os.path.join(self.root, 'processed', '%s.json' % fn.split('.')[0])
                       for fn in os.listdir(path)])

    @property
    def fault_report_names(self) -> Union[str, List[str], Tuple]:
        path = os.path.join(self.root, 'raw')
        return sorted([os.path.join(PROJECT_PATH, "experiment_result", 'pilot_report', '%s.json' % fn.split('.')[0])
                       for fn in os.listdir(path)])

    async def process_single_dapp(self, dapp, i):
        async with self.semaphore:
            dapp_name = dapp.get("name")
            local_mcp_client = MCPClient(MCP_SERVER_PATH)
            fault_report = ""
            try:
                await local_mcp_client.connect()
                processed_path = self.processed_data_names[i]
                report_path = self.fault_report_names[i]

                cached_processed_data = self.check_cache(dapp_name, processed_path)
                cached_fault_report = self.check_cache(dapp_name, report_path)
                if cached_fault_report:
                    print(f"✅ Cache hit for report: {dapp_name}")
                    return dapp_name, cached_fault_report

                if cached_processed_data:
                    processed_data = cached_processed_data
                    transaction_debugger = await self.reload_debugger(processed_data, local_mcp_client)
                else:
                    # Macro Analysis (Process)
                    print(f"🚀 Start processing: {dapp_name}")
                    processed_data, transaction_debugger = await DAppProcess(
                        net2rpc_bucket=self._net2rpc_bucket,
                        net2apikey_bucket=self._net2apikey_bucket,
                        mcp_client=local_mcp_client
                    ).process(dapp)
                    self.write_cache(dapp_name, processed_path, processed_data)

                # Micro Analysis (Analyze)
                if processed_data and transaction_debugger:
                    start_time = time.time()
                    fault_report, analyze_items = await DAppAnalyze(
                        processed_data=processed_data,
                        transaction_debugger=transaction_debugger,
                        net2apikey_bucket=self._net2apikey_bucket,
                        net2rpc_bucket=self._net2rpc_bucket,
                        mcp_client=local_mcp_client,
                        metrics_collector=self.metrics_collector
                    ).analyze()
                    if fault_report.startswith("ERROR"):
                        print(f"LLM Fault: {fault_report}")

                    elapsed_time = time.time() - start_time
                    final_output = {}
                    if analyze_items:
                        analyze_times = analyze_items["time"]
                        analyze_tokens = analyze_items["token"]

                        analyze_times["total_time"] = elapsed_time
                        final_output = {
                            "report": fault_report,
                            "time": {
                                "macro": processed_data["time_used"],
                                "micro": analyze_times
                            },
                            "token": {
                                "macro": processed_data["token_used"],
                                "micro": analyze_tokens
                            }
                        }
                    if final_output:
                        self.write_cache(dapp_name, report_path, final_output)
                    return dapp_name, fault_report
                return dapp_name, fault_report

            except Exception as e:
                import traceback
                print(f"❌ Error processing {dapp_name}: {e}")
                traceback.print_exc()

                error_stack = traceback.format_exc()
                error_log_dir = os.path.join(PROJECT_PATH, "experiment_result", 'pilot_report', 'errors')
                os.makedirs(error_log_dir, exist_ok=True)
                error_file_path = os.path.join(error_log_dir, f"{dapp_name}_error.log")

                with open(error_file_path, 'w', encoding='utf-8') as f:
                    f.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write(f"Error processing {dapp_name}:\n")
                    f.write(error_stack)

                print(f"❌ [FAILED] {dapp_name} - Log saved to {error_file_path}")
                if self.metrics_collector:
                    self.metrics_collector.finalize_case(dapp_name, "ERROR")
                return dapp_name, f"RUNTIME_ERROR: See log at {error_file_path}\nDetails: {str(e)}"
            finally:
                if 'transaction_debugger' in locals() and transaction_debugger:
                    try:
                        await transaction_debugger.close()
                    except Exception as e:
                        print(f"⚠️ Warning: Failed to close transaction_debugger: {e}")
                if local_mcp_client:
                    try:
                        await local_mcp_client.close()
                    except Exception as e:
                        print(f"⚠️ Warning: Failed to close MCP client: {e}")

    async def main_workflow(self):
        dapps = []
        for fn in self.raw_file_names:
            with open(fn, 'r', encoding="utf-8") as f:
                dapps.append(json.load(f))
        try:
            tasks = []
            for i, dapp in enumerate(dapps):
                if dapp["name"] != "SushiSwap":
                    continue
                task = asyncio.create_task(self.process_single_dapp(dapp, i))
                tasks.append(task)
            results = await asyncio.gather(*tasks)

            for name, _report in results:
                if _report:
                    self.dapp2report[name] = _report
            return self.dapp2report
        except Exception as e:
            raise e

    async def reload_debugger(self, processed_data, mcp_client):
        transaction_debugger = TraceAgent(
            processed_data,
            mcp_client=mcp_client,
            dapp_name=processed_data["dapp"]["name"]
        )
        await transaction_debugger.init()
        return transaction_debugger

    @staticmethod
    def check_cache(dapp_name, cache_file_path):
        if os.path.exists(cache_file_path):
            print(f'Loading cached data for {dapp_name} from {cache_file_path}')
            try:
                with open(cache_file_path, 'r', encoding='utf-8') as f:
                    cached_data = json.load(f)
                    return cached_data
            except Exception as e:
                print(f'Error loading cached data for {dapp_name}: {e}')
                return {}
        return {}

    @staticmethod
    def write_cache(dapp_name, cache_file_path, processed_data):
        try:
            os.makedirs(os.path.dirname(cache_file_path), exist_ok=True)
            with open(cache_file_path, 'w', encoding='utf-8') as f:
                json.dump(processed_data, f, ensure_ascii=False, indent=2)
            print(f'Saved cached data for {dapp_name} to {cache_file_path}')
        except Exception as e:
            print(f'Error saving cached data for {dapp_name}: {e}')


async def main():
    return await WorkFlow(semaphore_num=1).main_workflow()


if __name__ == '__main__':
    report = asyncio.run(main())
    print(f"The Final Localization Report: \n{report}")
