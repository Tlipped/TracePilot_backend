import logging
import re
from typing import List

# 注意：这里导入了上面定义的两个类
from utils.decompile import HeimdallWrapper, ExternalDecompiler

logger = logging.getLogger("EVMTools")


class EVMTools:
    @staticmethod
    def extract_selectors_regex(bytecode_hex: str) -> List[str]:
        if bytecode_hex.startswith("0x"):
            bytecode_hex = bytecode_hex[2:]
        matches = re.findall(r"63([0-9a-fA-F]{8})", bytecode_hex)
        return list(set([f"0x{m}" for m in matches]))

    @staticmethod
    async def analyze_bytecode(bytecode_hex: str) -> str:
        report = []

        openchain_client = ExternalDecompiler()
        heimdall = HeimdallWrapper()

        if not bytecode_hex or len(bytecode_hex) < 10:
            return "Error: Bytecode is empty or too short to analyze."

        resolved_map = {}
        try:
            raw_selectors = EVMTools.extract_selectors_regex(bytecode_hex)
            if raw_selectors:
                resolved_map = await openchain_client.resolve_selectors(raw_selectors)
        except Exception as e:
            logger.warning(f"Signature lookup failed: {e}")

        report.append("\n### Source Code Reconstruction (via Heimdall-rs)")

        success, result_content = await heimdall.decompile(bytecode_hex)

        if success:
            report.append("✅ **Decompilation Successful**")
            report.append("The following pseudo-code allows you to inspect the contract logic:")
            report.append("```solidity")
            report.append(result_content)
            report.append("```")
        else:
            report.append("❌ **Decompilation Failed**")
            report.append(f"Reason: {result_content}")
            report.append("\n> **Note:** TraceDebugger could not reconstruct the logic from bytecode.")
        if resolved_map:
            sig_section = ["### Identified Function Signatures",
                           "The following function selectors were found in the bytecode:"]
            for sel, name in resolved_map.items():
                sig_section.append(f"- `{sel}`: **{name}**")
            sig_section.append("---")
            report = sig_section + report
        elif not success:
            report.append("\nNo function signatures could be identified.")

        return "\n".join(report)
