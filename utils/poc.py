import re
from typing import List, Tuple

from eth_utils import to_checksum_address, is_hex_address


class PoCMiddleware:
    def __init__(self):
        self.address_pattern = re.compile(r'\b(0x)?[a-fA-F0-9]{40}\b')
        self.const_decl_pattern = re.compile(r'address\s+constant\s+(\w+)\s*=\s*(.*?);')
        self.start_prank_pattern = re.compile(r'vm\.startPrank\(')
        self.stop_prank_pattern = re.compile(r'vm\.stopPrank\(')

    def process(self, code: str) -> Tuple[str, List[str]]:
        issues = []

        code = self._fix_checksums(code)

        code, unicode_issues = self._fix_unicode_strings(code)
        issues.extend(unicode_issues)

        code, shadow_issues = self._fix_naming_conflicts(code)
        issues.extend(shadow_issues)
        code, log_issues = self._fix_console_logs(code)
        issues.extend(log_issues)

        prank_issues = self._audit_prank_logic(code)
        issues.extend(prank_issues)

        return code, issues

    def _fix_checksums(self, code: str) -> str:

        def replace_match(match):
            raw_str = match.group(0)
            if not raw_str.startswith("0x"):
                addr_candidate = "0x" + raw_str
            else:
                addr_candidate = raw_str

            try:
                if is_hex_address(addr_candidate):
                    return to_checksum_address(addr_candidate)
                return raw_str
            except Exception:
                return raw_str

        return self.address_pattern.sub(replace_match, code)

    def _fix_unicode_strings(self, code: str) -> Tuple[str, List[str]]:
        issues = []
        pattern = re.compile(r'\b(unicode|hex)?\s*(".*?")')

        def replacer(match):
            prefix = match.group(1)
            content_with_quotes = match.group(2)
            content_inner = content_with_quotes[1:-1]

            has_non_ascii = any(ord(c) > 127 for c in content_inner)

            if has_non_ascii:
                if prefix == 'unicode':
                    return match.group(0)
                if prefix == 'hex':
                    return match.group(0)

                issues.append(f"Fixed non-ASCII string: {content_inner[:10]}...")
                return f'unicode{content_with_quotes}'

            return match.group(0)

        new_code = pattern.sub(replacer, code)
        return new_code, issues

    def _fix_naming_conflicts(self, code: str) -> Tuple[str, List[str]]:
        issues = []
        lines = code.split('\n')
        new_lines = []

        blacklist = {"WETH", "USDC", "USDT", "DAI", "WBTC", "ERC20", "FACTORY", "ROUTER", "PAIR"}

        renamed_map = {}

        for line in lines:
            match = self.const_decl_pattern.search(line)
            if match:
                var_name = match.group(1)
                if var_name in blacklist or (var_name.isupper() and not var_name.endswith("_ADDR")):
                    new_name = var_name + "_ADDR"
                    renamed_map[var_name] = new_name
                    line = line.replace(f"address constant {var_name}", f"address constant {new_name}")
                    issues.append(f"Auto-Renamed constant '{var_name}' to '{new_name}' to avoid shadowing.")

            new_lines.append(line)

        cleaned_code = '\n'.join(new_lines)

        for old, new in renamed_map.items():
            cleaned_code = re.sub(rf'\b{old}\b', new, cleaned_code)

        return cleaned_code, issues

    def _fix_console_logs(self, code: str) -> Tuple[str, List[str]]:
        issues = []
        if re.search(r'console\.log\(.*?\+.*?\)', code):
            issues.append(
                "Detected string concatenation (+) in console.log. Solidity requires formatted strings (e.g., console.log('Val: %s', val)).")
        if "vm.toString" in code:
            issues.append(
                "Detected usage of 'vm.toString'. Ensure you are using it inside string concatenation or check for 'member not found' errors.")
        return code, issues

    def _audit_prank_logic(self, code: str) -> List[str]:
        issues = []
        start_count = len(self.start_prank_pattern.findall(code))
        stop_count = len(self.stop_prank_pattern.findall(code))

        if start_count != stop_count:
            issues.append(
                f"Prank mismatch detected: startPrank({start_count}) vs stopPrank({stop_count}). This may cause state pollution.")

        return issues


class FeedbackAnalyzer:
    @staticmethod
    def analyze(stdout: str, stderr: str) -> str:
        combined_log = (stdout + "\n" + stderr).lower()
        hints = []

        if "must use eoa" in combined_log or "tx.origin" in combined_log:
            hints.append(
                "[CRITICAL]: The target contract enforces `require(msg.sender == tx.origin)`. "
                "You MUST change your prank to `vm.startPrank(attacker, attacker);` to verify tx.origin."
            )

        if "transfer amount exceeds balance" in combined_log or "transfer failed" in combined_log:
            hints.append(
                "[LOGIC]: Transfer failed due to insufficient balance. "
                "1. If dealing with Rebase tokens (DIGG/AMPL), DO NOT use `deal()`. Use `vm.prank(whale)` to transfer from a holder. "
                "2. Check if you approved the router/target before calling functions."
            )

        if "panic: arithmetic underflow" in combined_log or "0x11" in combined_log:
            hints.append(
                "[LOGIC]: Arithmetic overflow/underflow detected. "
                "This often happens with Rebase tokens when using `deal()` which breaks internal accounting. "
                "Switch to stealing tokens from a whale address."
            )

        if "declaration shadows" in combined_log:
            hints.append(
                "[SYNTAX]: Variable name shadowing detected. "
                "Please rename your `address constant` variables with a `_ADDR` suffix (e.g., `WETH` -> `WETH_ADDR`)."
            )

        if "member" in combined_log and "not found" in combined_log:
            hints.append(
                "[SYNTAX]: Member/Function not found. "
                "1. Check if the interface definition is missing a function signature. "
                "2. If using `console.log`, ensure you are NOT using string concatenation `+` but formatting `%s`."
            )

        if "invalid type for argument" in combined_log:
            hints.append(
                "[SYNTAX]: Invalid argument types. "
                "Check your `abi.encode` or function calls. Ensure address literals have correct checksums."
            )

        if "explicit type conversion not allowed" in combined_log and "payable" in combined_log:
            hints.append(
                "[SYNTAX CRITICAL]: Solidity 0.8+ requires explicit payable casting for interfaces with payable functions. "
                "You are trying to cast a non-payable `address` to a payable interface. "
                "FIX: Change `InterfaceName(ADDRESS_VAR)` to `InterfaceName(payable(ADDRESS_VAR))`."
            )

        if "invalid character in string" in combined_log or "unicode" in combined_log:
            hints.append(
                "[SYNTAX]: Invalid character (Chinese/Emoji) in string detected. "
                "Solidity 0.8+ requires the `unicode` prefix for non-ASCII strings. "
                "FIX: Change \"中文\" to unicode\"中文\", OR strictly use English logs."
            )

        if not hints:
            hints.append(
                "[GENERAL]: The test failed without a specific known pattern. "
                "Please check the stack trace above. Ensure you are forking from the correct block number."
            )

        return "\n".join(hints)
