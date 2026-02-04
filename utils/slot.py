from typing import Set, Any, List

from eth_abi import encode
from eth_utils import keccak, is_address, to_bytes

from entities.trace import MixTraceItem

class ResolvedVariable:
    def __init__(self, name: str, full_type: str, keys: List[Any]):
        self.name = name
        self.full_type = full_type
        self.keys = keys

    @property
    def label(self) -> str:
        if not self.keys:
            return self.name
        key_str = "".join([f"[{k}]" for k in self.keys])
        return f"{self.name}{key_str}"

    def __str__(self):
        return self.label


class StorageResolver:
    @staticmethod
    def calculate_mapping_slot(base_slot_hex: str, keys: list) -> str:
        current_slot = int(base_slot_hex, 16)
        for key in keys:
            if isinstance(key, str) and is_address(key):
                encoded_key = encode(['address'], [key])
            elif isinstance(key, int):
                encoded_key = encode(['uint256'], [key])
            else:
                encoded_key = encode(['bytes32'], [to_bytes(hexstr=key)])

            encoded_slot = encode(['uint256'], [current_slot])
            current_slot = int.from_bytes(keccak(encoded_key + encoded_slot), byteorder='big')
        return hex(current_slot)

    @staticmethod
    def collect_potential_keys(trace_item: MixTraceItem) -> Set[Any]:
        keys = set()
        keys.add(trace_item.from_addr.lower())
        keys.add(trace_item.to_addr.lower())

        for var in trace_item.decoded_input:
            if var.type == 'address':
                keys.add(var.value.lower())

        # 从内部事件中提取 (如果当前节点有事件)
        for event in trace_item.events:
            for var in event.variables:
                if var.type == 'address':
                    keys.add(var.value.lower())
        return keys

    # def _get_variable_info(self, addr: str, raw_slot: str, item: MixTraceItem) -> Optional[ResolvedVariable]:
    #     entity = self.entity_map.get(addr.lower())
    #     if not entity or not entity.states:
    #         return None
    #
    #     potential_keys = list(StorageResolver.collect_potential_keys(item))
    #     target_int = int(raw_slot, 16)
    #
    #     for state in entity.states:
    #         base_index = state.get("index")
    #         if not base_index: continue
    #
    #         var_name = state["name"]
    #         var_type = state["type"]
    #         depth = str(var_type).count("mapping")
    #
    #         if depth == 0:
    #             if int(base_index, 16) == target_int:
    #                 return ResolvedVariable(var_name, var_type, [])
    #             continue
    #
    #         for key_combo in itertools.product(potential_keys, repeat=depth):
    #             calculated_slot = StorageResolver.calculate_mapping_slot(base_index, list(key_combo))
    #             if int(calculated_slot, 16) == target_int:
    #                 return ResolvedVariable(var_name, var_type, list(key_combo))
    #     return None
