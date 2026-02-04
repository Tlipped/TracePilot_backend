from decimal import Decimal
from typing import Dict, Any


class LogAnalyzer:
    def __init__(self, token_db: Dict):
        self.token_db = {k.lower(): v for k, v in token_db.items()}

    def _fmt_addr(self, address: str) -> str:
        if not address: return "None"
        addr_lower = address.lower()
        info = self.token_db.get(addr_lower)
        return f"{info['name']}({addr_lower})" if info else addr_lower

    def _fmt_amt(self, amount_raw: Any, token_address: str) -> str:
        try:
            raw_str = str(amount_raw)
            amt_decimal = Decimal(raw_str)

            info = self.token_db.get(token_address.lower())
            decimals = info['decimals'] if info else 18
            symbol = info['name'] if info else "Units"
            val = amt_decimal / Decimal(10 ** decimals)
            readable_val = "{:f}".format(val.normalize())

            return f"{readable_val} {symbol} (Raw: {raw_str}, Decimals: {decimals}, Address: {token_address})"

        except Exception as e:
            return f"{str(amount_raw)} (Address: {token_address})"

    def process_log(self, log: Dict[str, Any]) -> str:
        event_name = log.get("name", "UnknownEvent")
        contract_addr = log.get("raw", {}).get("address", "").lower()

        raw_inputs = log.get("inputs")
        safe_inputs = raw_inputs if raw_inputs is not None else []
        inputs = {i["soltype"]["name"]: i["value"] for i in safe_inputs if "soltype" in i}

        if event_name == "Transfer":
            frm = inputs.get("from") or inputs.get("src")
            to = inputs.get("to") or inputs.get("dst")
            val = inputs.get("value") or inputs.get("wad")

            action = "TRANSFER"
            if frm == "0x0000000000000000000000000000000000000000":
                action = "MINT"
            elif to == "0x0000000000000000000000000000000000000000":
                action = "BURN"

            return f"[{action}] {self._fmt_amt(val, contract_addr)} | {self._fmt_addr(frm)} -> {self._fmt_addr(to)}"

        elif event_name == "Approval":
            owner = inputs.get("owner") or inputs.get("src")
            spender = inputs.get("spender") or inputs.get("guy")
            val = inputs.get("value") or inputs.get("wad")
            return f"[APPROVE] {self._fmt_addr(owner)} allowed {self._fmt_addr(spender)} for {self._fmt_amt(val, contract_addr)}"

        elif event_name == "Swap":
            return f"[SWAP] Contract: {self._fmt_addr(contract_addr)} | Details: {inputs}"

        params = ", ".join([f"{k}: {v}" for k, v in inputs.items()])
        return f"[{event_name.upper()}] {self._fmt_addr(contract_addr)} | {params}"
