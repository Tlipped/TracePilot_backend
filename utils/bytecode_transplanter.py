# utils/transplanter.py
import logging
from typing import Dict, Tuple

from daos.contract import ContractCompileItem

logger = logging.getLogger("Transplanter")


class BytecodeTransplanter:
    @staticmethod
    def transplant(
            original_compile_item: ContractCompileItem,
            on_chain_runtime_bytecode_hex: str,
            patched_compile_item: ContractCompileItem
    ) -> Tuple[bool, str, str]:
        try:
            if not on_chain_runtime_bytecode_hex or len(on_chain_runtime_bytecode_hex) <= 2:
                return True, patched_compile_item.bytecode, "[Info] No on-chain code found. Skipping transplant."

            if not original_compile_item.immutable_references:
                return True, patched_compile_item.bytecode, "[Info] No immutables in original contract. Skipping transplant."
            original_values, extract_log = BytecodeTransplanter._extract_immutable_values(
                original_compile_item, on_chain_runtime_bytecode_hex
            )

            final_bytecode, inject_log = BytecodeTransplanter._inject_immutable_values(
                patched_compile_item, original_values
            )

            logs = f"--- Extraction Log ---\n{extract_log}\n--- Injection Log ---\n{inject_log}"
            return True, final_bytecode, logs

        except Exception as e:
            import traceback
            err_msg = f"Transplantation failed: {str(e)}\n{traceback.format_exc()}"
            logger.error(err_msg)
            return False, patched_compile_item.bytecode, err_msg

    @staticmethod
    def _extract_immutable_values(compile_item, bytecode_hex: str) -> Tuple[Dict[str, str], str]:
        logs = []
        if bytecode_hex.startswith("0x"):
            bytecode_hex = bytecode_hex[2:]

        bytecode_bytes = bytes.fromhex(bytecode_hex)
        immutable_refs = compile_item.immutable_references
        ast = compile_item.ast

        id_to_name = BytecodeTransplanter._map_ast_id_to_name(ast)

        extracted_values = {}  # {var_name: hex_value}

        for ast_id_str, refs in immutable_refs.items():
            ast_id = int(ast_id_str)
            var_name = id_to_name.get(ast_id)

            if not var_name:
                logs.append(f"[Warn] Immutable with AST ID {ast_id} has no name map found. Skipping.")
                continue

            if not refs:
                continue

            ref = refs[0]
            start = ref['start']
            length = ref['length']

            if start + length > len(bytecode_bytes):
                logs.append(
                    f"[Error] Offset out of bounds for {var_name}. Bytecode len: {len(bytecode_bytes)}, Need: {start + length}")
                continue

            value_bytes = bytecode_bytes[start: start + length]
            extracted_values[var_name] = value_bytes.hex()
            logs.append(f"[Extract] Found {var_name} (len={length}): 0x{value_bytes.hex()}")

        return extracted_values, "\n".join(logs)

    @staticmethod
    def _inject_immutable_values(compile_item, value_map: Dict[str, str]) -> Tuple[str, str]:
        logs = []
        base_bytecode_hex = compile_item.bytecode
        if not base_bytecode_hex:
            raise ValueError("Patched contract has no bytecode generated.")

        if base_bytecode_hex.startswith("0x"):
            base_bytecode_hex = base_bytecode_hex[2:]

        final_bytes = bytearray.fromhex(base_bytecode_hex)

        immutable_refs = compile_item.immutable_references
        ast = compile_item.ast
        id_to_name = BytecodeTransplanter._map_ast_id_to_name(ast)

        for ast_id_str, refs in immutable_refs.items():
            ast_id = int(ast_id_str)
            var_name = id_to_name.get(ast_id)

            if not var_name:
                continue

            if var_name not in value_map:
                logs.append(
                    f"[Warn] Immutable '{var_name}' found in Patch but missing in Original. Keeping default (0).")
                continue

            val_hex = value_map[var_name]
            val_bytes = bytes.fromhex(val_hex)

            for ref in refs:
                start = ref['start']
                length = ref['length']

                if len(val_bytes) != length:
                    if len(val_bytes) < length:
                        logs.append(
                            f"[Warn] Size mismatch for {var_name} (Original {len(val_bytes)} < Patch {length}). Padding zeros.")
                        val_bytes = val_bytes.rjust(length, b'\0')
                    else:
                        logs.append(
                            f"[Warn] Size mismatch for {var_name} (Original {len(val_bytes)} > Patch {length}). Truncating.")
                        val_bytes = val_bytes[-length:]

                final_bytes[start: start + length] = val_bytes

            logs.append(f"[Inject] Injected {var_name} into {len(refs)} slots.")

        return "0x" + final_bytes.hex(), "\n".join(logs)

    @staticmethod
    def _map_ast_id_to_name(ast_root: Dict) -> Dict[int, str]:
        mapping = {}

        def _recursive_find(node):
            if not isinstance(node, dict):
                return

            # nodeType: VariableDeclaration, mutability: immutable
            if node.get("nodeType") == "VariableDeclaration":
                if node.get("mutability") == "immutable":
                    node_id = node.get("id")
                    name = node.get("name")
                    if node_id is not None and name:
                        mapping[int(node_id)] = name

            for _key, value in node.items():
                if isinstance(value, dict):
                    _recursive_find(value)
                elif isinstance(value, list):
                    for item in value:
                        _recursive_find(item)

        if not ast_root:
            return {}

        for key, val in ast_root.items():
            if isinstance(val, dict):
                _recursive_find(val)

        return mapping
