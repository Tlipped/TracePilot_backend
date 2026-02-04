import json
import asyncio
import aiohttp
import re
from typing import List, Any, Dict, Optional, Tuple

from eth_utils import to_hex
from web3 import Web3
from web3.exceptions import Web3Exception

from downloaders.contract import ContractABIDownloader
from settings import WEB3_PROVIDER, SIGNATURE_PATH

from decimal import Decimal
import re

KNOWN_DECIMALS = {
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": 18,  # WETH
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": 6,  # USDC
    "0xdac17f958d2ee523a2206206994597c13d831ec7": 6,  # USDT
    "0xae7ab96520de3a18e5e111b5eaab095312d7fe84": 18,  # stETH
}

COMMON_SELECTORS: Dict[str, str] = {
    # --- ERC20 / ERC721 Standard ---
    "a9059cbb": "transfer(address,uint256)",
    "23b872dd": "transferFrom(address,address,uint256)",
    "095ea7b3": "approve(address,uint256)",
    "70a08231": "balanceOf(address)",
    "18160ddd": "totalSupply()",
    "dd62ed3e": "allowance(address,address)",
    "42842e0e": "safeTransferFrom(address,address,uint256)",
    "b88d4fde": "safeTransferFrom(address,address,uint256,bytes)",
    "a22cb465": "setApprovalForAll(address,bool)",

    # --- Metadata ---
    "06fdde03": "name()",
    "95d89b41": "symbol()",
    "313ce567": "decimals()",
    "c87b56dd": "tokenURI(uint256)",

    # --- WETH / Native Wrap ---
    "d0e30db0": "deposit()",
    "2e1a7d4d": "withdraw(uint256)",

    # --- Uniswap V2 / V3 / Swap ---
    "0902f1ac": "getReserves()",
    "fff6cae9": "notifyRewardAmount(uint256)",
    "68b56f91": "swap(uint256,uint256,address,bytes)",  # Uniswap V2 pair swap
    "5909c0d5": "swap(address,bool,int256,uint160,bytes)",  # Uniswap V3 swap
    "38ed1739": "swapExactTokensForTokens(uint256,uint256,address[],address,uint256)",
    "18cbafe5": "swapExactTokensForETH(uint256,uint256,address[],address,uint256)",
    "7ff36ab5": "swapExactETHForTokens(uint256,address[],address,uint256)",

    # --- Proxy / Implementation ---
    "5c60da1b": "implementation()",
    "3659cfe6": "upgradeTo(address)",
    "4f1ef286": "upgradeToAndCall(address,bytes)",

    # --- Common Ownership ---
    "8da5cb5b": "owner()",
    "f2fde38b": "transferOwnership(address)",
    "715018a6": "renounceOwnership()",

    # --- Initialization ---
    "8129fc1c": "initialize()",
    "c4d66de8": "initialize(address)",
}


def hex_to_int(hex_str):
    if not hex_str or hex_str == '0x':
        return 0
    return int(hex_str, 16)


def format_value(value_wei, decimals=18):
    if value_wei == 0:
        return "0"
    val = Decimal(value_wei) / Decimal(10 ** decimals)
    if val >= 1000:
        return f"{val:.2f}"
    return f"{val:.6f}".rstrip('0').rstrip('.')


def format_arg_bytes(arg_value):
    if isinstance(arg_value, (bytes, bytearray)):
        return '0x' + arg_value.hex()
    return arg_value


class ParseSignature:
    def __init__(self, apikey: str):
        self.apikey = apikey
        self.w3 = Web3(Web3.HTTPProvider(WEB3_PROVIDER, request_kwargs={'timeout': 10}))

        if not self.w3.is_connected():
            print("Warning: Failed to connect to Ethereum node!")

        self.function_signatures = {}
        self.event_signatures = {}

        self.load_function_signatures()

    def load_function_signatures(self):
        try:
            with open(SIGNATURE_PATH, 'r', encoding='utf-8') as signature_file:
                signature_data = signature_file.read()
            for row in signature_data.split('\n')[1:]:
                if not row.strip(): continue
                parts = row.split(',')
                if len(parts) < 3: continue
                text = parts[0].strip().strip('"')
                sign = parts[1].strip()
                sig_type = parts[2].strip()

                if sig_type == 'Function':
                    self.function_signatures[sign] = text
                elif sig_type == 'Event':
                    self.event_signatures[sign] = text
        except FileNotFoundError:
            # print(f"Info: Signature file not found at {SIGNATURE_PATH}")
            pass
        except Exception as e:
            print(f"Error loading signatures: {e}")

    async def _fetch_signature_from_apis(self, selector: str) -> Optional[str]:
        if not selector or len(selector) != 10:
            return None

        if selector in self.function_signatures:
            return self.function_signatures[selector]

        async with aiohttp.ClientSession() as session:
            # 1. Try OpenChain (sig.eth)
            try:
                url = f"https://api.openchain.xyz/signature-database/v1/lookup?function={selector}&filter=true"
                async with session.get(url, timeout=3) as response:
                    if response.status == 200:
                        data = await response.json()
                        result = data.get('result', {}).get('function', {}).get(selector, [])
                        if result:
                            return result[0].get('name')
            except Exception:
                pass

            # 2. Try 4byte.directory
            try:
                url = f"https://www.4byte.directory/api/v1/signatures/?hex_signature={selector}"
                async with session.get(url, timeout=3) as response:
                    if response.status == 200:
                        data = await response.json()
                        results = data.get('results', [])
                        if results:
                            # id 越小越可能是原始定义
                            results.sort(key=lambda x: x.get('id', 99999999))
                            return results[0].get('text_signature')
            except Exception:
                pass

            try:
                url = f"https://api.etherface.io/v1/signatures/hash/{selector}"
                async with session.get(url, timeout=3) as response:
                    if response.status == 200:
                        data = await response.json()
                        items = data.get('items', [])
                        if items:
                            return items[0].get('text')
            except Exception:
                pass

        return None

    def _recursive_format(self, value: Any, address2name: Dict[str, str]) -> Any:
        if isinstance(value, (bytes, bytearray)):
            return to_hex(value)

        if isinstance(value, str):
            if value.startswith('0x'):
                val_lower = value.lower()
                if val_lower in address2name:
                    return address2name[val_lower]
            return value

        if isinstance(value, (list, tuple)):
            return [self._recursive_format(item, address2name) for item in value]

        return value

    def _safe_split_params(self, param_str: str) -> List[str]:
        params = []
        depth = 0
        current_chunk = []

        for char in param_str:
            if char == '(':
                depth += 1
                current_chunk.append(char)
            elif char == ')':
                depth -= 1
                current_chunk.append(char)
            elif char == ',' and depth == 0:
                p = "".join(current_chunk).strip()
                if p: params.append(p)
                current_chunk = []
            else:
                current_chunk.append(char)

        if current_chunk:
            p = "".join(current_chunk).strip()
            if p: params.append(p)

        return params

    def _decode_manual(self, signature_text: str, input_hex_data: str, address2name: dict) -> str:
        try:
            if '(' not in signature_text or not signature_text.endswith(')'):
                return signature_text

            func_name = signature_text[:signature_text.find('(')]
            params_content = signature_text[signature_text.find('(') + 1: -1]

            if not params_content.strip():
                param_types = []
            else:
                param_types = self._safe_split_params(params_content)

            if input_hex_data.startswith('0x'):
                bs = bytes.fromhex(input_hex_data[2:])
            else:
                bs = bytes.fromhex(input_hex_data)

            if len(bs) < 4:
                return f"{func_name}()"

            args_data = bs[4:]

            decoded_values = self.w3.codec.decode(param_types, args_data)

            formatted_args = []
            for i, val in enumerate(decoded_values):
                type_name = param_types[i]
                val_fmt = self._recursive_format(val, address2name)
                formatted_args.append(f"{type_name}={val_fmt}")

            args_str = ", ".join(formatted_args)
            return f"{func_name}({args_str})"

        except Exception as e:
            return f"{signature_text} [Decode Failed]"

    async def parse_function_sig(self, input_hex_data: str, output_hex_data: str, contract_address: str,
                                 address2name: dict) -> str:
        if not input_hex_data or input_hex_data == '0x':
            return "()"

        contract_address = str(contract_address).lower()
        selector = input_hex_data[:10]

        contract_name = address2name.get(contract_address, contract_address)
        if contract_name == "":
            contract_name = contract_address

        contract_abi = await ContractABIDownloader(apikey=self.apikey).download(contract_address=contract_address)

        async def handle_fallback_decoding():
            sig_text = self.function_signatures.get(selector)

            if not sig_text:
                sig_text = await self._fetch_signature_from_apis(selector)
                if sig_text:
                    self.function_signatures[selector] = sig_text

            if sig_text:
                decoded_str = self._decode_manual(sig_text, input_hex_data, address2name)
                return f"{contract_name}.{decoded_str}".strip()

            return f"{contract_name}.{selector}"

        if contract_abi is None:
            return await handle_fallback_decoding()

        try:
            contract = self.w3.eth.contract(abi=contract_abi)
            func_obj, func_params_dict = contract.decode_function_input(input_hex_data)
        except (ValueError, Web3Exception):
            return await handle_fallback_decoding()

        formatted_params = []
        inputs_abi = func_obj.abi.get('inputs', [])
        type_map = {item.get('name', ''): item.get('type', 'arg') for item in inputs_abi}

        for arg_name, arg_value in func_params_dict.items():
            arg_type = type_map.get(arg_name, 'unknown')
            fmt_val = self._recursive_format(arg_value, address2name)

            display_name = arg_name if arg_name else arg_type
            formatted_params.append(f"{display_name}={fmt_val}")

        args_str = ", ".join(formatted_params)
        function_name = func_obj.abi.get("name", "Unknown")

        output_str = ""
        outputs_abi = func_obj.abi.get("outputs", [])

        if output_hex_data and output_hex_data != '0x' and outputs_abi:
            try:
                output_types = [param['type'] for param in outputs_abi]

                if output_hex_data.startswith('0x'):
                    output_bytes = bytes.fromhex(output_hex_data[2:])
                else:
                    output_bytes = bytes.fromhex(output_hex_data)

                decoded_outputs = self.w3.codec.decode(output_types, output_bytes)

                formatted_outputs = []
                for i, out_val in enumerate(decoded_outputs):
                    out_name = outputs_abi[i].get('name', '')
                    out_type = outputs_abi[i].get('type', '')
                    fmt_val = self._recursive_format(out_val, address2name)

                    if out_name:
                        formatted_outputs.append(f"{out_name}={fmt_val}")
                    else:
                        formatted_outputs.append(f"{out_type}={fmt_val}")

                output_str = ", ".join(formatted_outputs)
            except Exception:
                output_str = ""

        result_str = f"{contract_name}.{function_name}({args_str})"
        if output_str:
            result_str += f" -> ({output_str})"
        result_str += f" [{contract_address}]"

        return result_str
