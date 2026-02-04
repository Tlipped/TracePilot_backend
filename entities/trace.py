from typing import Optional, List, Dict, Any

from utils.signature import hex_to_int, COMMON_SELECTORS


class SourceLocation:
    def __init__(self, pc: int, op: str, file_index: int, line_number: int,
                 code_start: int, code_length: int):
        self.pc = pc
        self.op = op
        self.file_index = file_index
        self.line_number = line_number
        self.code_start = code_start
        self.code_length = code_length


class SolidityVariable:
    def __init__(self, name: str, sol_type: str, value: Any, slot_index: str = None):
        self.name = name
        self.type = sol_type
        self.value = value
        self.slot_index = slot_index
        self.decoded_value = ""

    def __repr__(self):
        val_to_show = self.decoded_value if self.decoded_value != '' else self.value

        if isinstance(val_to_show, str) and len(val_to_show) > 512:
            val_to_show = f"{val_to_show[:66]}...[size={len(val_to_show)}]...{val_to_show[-64:]}"

        slot_info = f" [slot:{self.slot_index}]" if self.slot_index else ""
        return f"{self.type} {self.name}={val_to_show}"

    def __str__(self):
        return self.__repr__()


class Log:
    def __init__(self, node_data: Dict[str, Any]):
        self.absolute_position = node_data.get("absolute_position", -1)
        self.contract_name = node_data.get("contract_name", "Unknown")
        self.call_type = node_data.get("call_type", "LOG")

        self.loc = SourceLocation(
            pc=node_data.get("caller_pc", -1),
            op=node_data.get("caller_op", ""),
            file_index=node_data.get("caller_file_index", -1),
            line_number=node_data.get("caller_line_number", -1),
            code_start=node_data.get("caller_code_start", -1),
            code_length=node_data.get("caller_code_length", -1)
        )
        self.source_snippet: str = ""

        log_body = node_data.get("log") or {}
        self.event_name = log_body.get("name", "Unknown")
        self.anonymous = log_body.get("anonymous", False)
        self.variables: List[SolidityVariable] = []
        for item in log_body.get("inputs") or []:
            sol = item.get("soltype") or {}
            self.variables.append(SolidityVariable(
                sol.get("name", ""), sol.get("type", ""), item.get("value"), sol.get("index")
            ))

        raw = log_body.get("raw") or {}
        self.contract_address = raw.get("address")
        self.topics = raw.get("topics") or []
        self.data = raw.get("data")

    def get_variable_by_name(self, name: str) -> Any:
        for var in self.variables:
            if var.name == name:
                return var.value
        return None

    def __repr__(self):
        vars_str = ", ".join([str(v) for v in self.variables])
        code = f" | Code: `{self.source_snippet}`" if self.source_snippet else ""
        return f"[{self.absolute_position}] LOG {self.event_name}({vars_str}){code}"


class StorageOp:
    def __init__(self, node_data: Dict[str, Any]):
        self.absolute_position = node_data.get("absolute_position", -1)
        self.call_type = node_data.get("call_type", "UNKNOWN")
        self.address = node_data.get("storage_address") or node_data.get("address")

        self.loc = SourceLocation(
            pc=node_data.get("caller_pc", -1),
            op=node_data.get("caller_op", ""),
            file_index=node_data.get("caller_file_index", -1),
            line_number=node_data.get("caller_line_number", -1),
            code_start=node_data.get("caller_code_start", -1),
            code_length=node_data.get("caller_code_length", -1)
        )
        self.source_snippet: str = ""
        self.source_line: str = ""

        slots = node_data.get("storage_slot") or []
        originals = node_data.get("storage_value_original") or []
        dirties = node_data.get("storage_value_dirty") or []
        self.slot = slots[0] if slots else None
        self.val_before = originals[0] if originals else None
        self.val_after = dirties[0] if dirties and self.call_type == "SSTORE" else self.val_before

        # self.resolved_var: Optional[ResolvedVariable] = None

    def is_write(self) -> bool:
        return self.call_type == "SSTORE"

    def is_changed(self) -> bool:
        return self.val_before != self.val_after

    def get_code(self):
        cleaned_line = "".join(self.source_line.replace(";", "").split()) if self.source_line else ""
        cleaned_snip = "".join(self.source_snippet.split()) if self.source_snippet else ""
        show_snippet = self.source_snippet and cleaned_line != cleaned_snip
        code = f" [`{self.source_snippet.strip()}`]" if show_snippet else ""
        line = f"  Code: `{self.source_line.strip()}`" if self.source_line else ""
        return f"{line}{code}"

    def __repr__(self):
        op_label = "WRITE" if self.call_type == "SSTORE" else "READ"
        # location_desc = self.resolved_var.label if self.resolved_var else f"Slot[{self.slot}]"
        location_desc = f"Slot[{self.slot}]"
        val_str = f"{self.val_before} -> {self.val_after}" if op_label == "WRITE" else f"{self.val_before}"
        return f"[{self.absolute_position}] {op_label} {location_desc} = {val_str}{self.get_code()}"


class MixTraceItem:
    def __init__(self, data: Dict, parent: Optional['MixTraceItem'] = None, depth: int = 0):
        self.hash: str = data.get('hash', '')
        self.contract_name: str = data.get('contract_name', '')
        if not self.contract_name:
            self.contract_name = f"Contract_{data.get('to', '')[:6]}"

        self.function_name: str = data.get('function_name', '')
        self.input_data: str = data.get('input', '0x')
        self.selector = "0x"
        if len(self.input_data) >= 10:
            self.selector = self.input_data[:10].lower()

        if not self.function_name and self.selector != "0x":
            raw_sel = self.selector[2:]
            if raw_sel in COMMON_SELECTORS:
                self.function_name = f"{COMMON_SELECTORS[raw_sel]}? selector: {self.selector}"
            else:
                self.function_name = f"{self.selector} (Unverified)"

        self.call_type: str = data.get('call_type', '')
        self.absolute_position = data.get('absolute_position', -1)

        self.parent = parent
        self.depth = depth

        self.function_loc = SourceLocation(
            pc=data.get('function_pc', -1),
            op=data.get('function_op', ''),
            file_index=data.get('function_file_index', -1),
            line_number=data.get('function_line_number', -1),
            code_start=data.get('function_code_start', -1),
            code_length=data.get('function_code_length', -1)
        )

        self.caller_loc = SourceLocation(
            pc=data.get('caller_pc', -1),
            op=data.get('caller_op', ''),
            file_index=data.get('caller_file_index', -1),
            line_number=data.get('caller_line_number', -1),
            code_start=data.get('caller_code_start', -1),
            code_length=data.get('caller_code_length', -1)
        )

        self.contract_source: str = ""
        self.function_source: str = ""
        self.caller_source: str = ""

        self.from_name = ""
        self.to_name = ""

        self.address: str = data.get('address', '')
        self.from_addr: str = data.get('from', '')
        self.to_addr: str = data.get('to', '')
        self.storage_address: str = data.get('storage_address', '')
        self.from_balance: Optional[str] = data.get('from_balance')
        self.to_balance: Optional[str] = data.get('to_balance')
        self.value_transferred: Optional[str] = data.get('value')

        self.input_data: str = data.get('input', '0x')
        self.output_data: str = data.get('output', '0x')

        self.function_variables: List[SolidityVariable] = self._parse_vars(data.get('function_variables', []))
        self.caller_variables: List[SolidityVariable] = self._parse_vars(data.get('caller_variables', []))
        self.decoded_input: List[SolidityVariable] = self._parse_vars(data.get('decoded_input', []))
        self.decoded_output: List[SolidityVariable] = self._parse_vars(data.get('decoded_output', []))

        if not self.decoded_input and len(self.input_data) > 10:
            raw_len = len(self.input_data)
            is_creation = self.call_type in ["CREATE", "CREATE2"]
            is_huge_data = raw_len > 1024

            var_name = "raw_data"
            display_val = ""

            if is_creation:
                var_name = "init_code"
                byte_size = (raw_len - 2) // 2
                display_val = f"{self.input_data[:10]}...[Creation Bytecode: {byte_size} bytes]...{self.input_data[-10:]}"

            elif is_huge_data:
                display_val = f"{self.input_data[:34]}...[Total Len: {raw_len}]...{self.input_data[-32:]}"
            raw_var = SolidityVariable(
                name=var_name,
                sol_type="bytes",
                value=self.input_data,
                slot_index=None
            )
            if display_val:
                raw_var.decoded_value = display_val
            self.decoded_input.append(raw_var)

        self.gas: int = data.get('gas', -1)
        self.gas_used: int = data.get('gas_used', -1)
        self.refund_gas: int = data.get('refund_gas', -1)

        self.storage_ops: List[StorageOp] = []
        self.events: List[Log] = []
        self.calls: List['MixTraceItem'] = []

    def _parse_vars(self, var_list: Optional[List[Dict]]) -> List[SolidityVariable]:
        results = []
        if not var_list:
            return results
        for v in var_list:
            soltype = v.get('soltype', {})
            results.append(SolidityVariable(
                name=soltype.get('name', ''),
                sol_type=soltype.get('type', ''),
                value=v.get('value'),
                slot_index=soltype.get('index')
            ))
        return results

    def get_name(self):
        if self.from_name and self.to_name and (self.from_name != self.to_name):
            return f"({self.from_name} => {self.to_name})"
        if self.contract_name:
            return self.contract_name
        if self.to_name:
            return self.to_name
        return self.to_addr[:10]

    def __str__(self) -> str:
        return f"[{self.absolute_position}] [{self.call_type}] {self.get_name()}.{self.function_name}({', '.join(str(_input) for _input in self.decoded_input)}) -> ({', '.join(str(_output) for _output in self.decoded_output)})"


class TraceNode:
    def __init__(self, item: MixTraceItem, depth: int, node_type: str):
        self.pos = item.absolute_position  # mix tree
        self.depth = depth  # depth in tree (External Tree | Node Mix Tree)
        self.address_to = item.to_addr
        self.trace_type = item.call_type
        self.node_type = node_type  # "EXTERNAL" or "INTERNAL"

        self.gas_used = item.gas_used
        self.value = hex_to_int(item.value_transferred) if item.value_transferred else 0

        self.comment = ""
        self.view_expanded = False
        self.children: List['TraceNode'] = []
        self.mix_trace_item: MixTraceItem = item

        inputs = ", ".join([str(v) for v in item.decoded_input]) if item.decoded_input else ""
        outputs = ", ".join([str(o) for o in item.decoded_output]) if item.decoded_output else ""
        self.node_name = f"[{item.call_type}] {item.get_name()}.{item.function_name}({inputs}) -> ({outputs}) [{item.to_addr}]"

    def get_name(self):
        return f"[{self.pos}] {self.node_name}"

    def format_node(self):
        base_info = self.get_name()
        gas_info = f"Gas: {self.gas_used}" if self.gas_used is not None else ""

        extras = []
        if gas_info:
            extras.append(gas_info)

        if self.value and self.value > 0:
            val_eth = self.value / 1e18
            if val_eth < 0.0001:
                extras.append(f"Val: <0.0001 ETH")
            else:
                extras.append(f"Val: {val_eth:.4f} ETH")

        item = self.mix_trace_item
        if len(item.events) > 0:
            extras.append(f"Logs: {len(item.events)}")
        if len(item.storage_ops) > 0:
            sstore_cnt = sum(1 for op in item.storage_ops if op.call_type == 'SSTORE')
            sload_cnt = sum(1 for op in item.storage_ops if op.call_type == 'SLOAD')
            if sstore_cnt > 0 or sload_cnt > 0:
                extras.append(f"[S: {sstore_cnt}/L: {sload_cnt}]")

        extra_str = f" ({', '.join(extras)})" if extras else ""

        if self.comment != "":
            extra_str += f"  // {self.comment}"
        return f"{base_info}{extra_str}"
