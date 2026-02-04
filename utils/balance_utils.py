from collections import defaultdict
from decimal import Decimal
from typing import Dict, Tuple, Any, Set

from utils.price import get_token_price, platform_to_chain
from utils.signature import hex_to_int
from utils.token_utils import ERC20_TRANSFER_TOPIC, parse_address, format_amount

NATIVE_TOKEN_ADDRESS = '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
CHAIN_CONFIG = {
    'ethereum': {
        'wrapped': '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',  # WETH
        'symbol': 'ETH',
        'name': 'Ether'
    },
    'bsc': {
        'wrapped': '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c',  # WBNB
        'symbol': 'BNB',
        'name': 'BNB'
    }
}


class BalanceAnalyzer:
    @staticmethod
    def safe_int(value: Any) -> int:
        if value is None:
            return 0
        try:
            if isinstance(value, (int, float)):
                return int(value)
            s = str(value).strip()
            if s.startswith('0x'):
                return int(s, 16)
            return int(s)
        except:
            return 0

    @classmethod
    def parse_tenderly_data(cls, macro_data: Dict) -> Tuple[Dict[str, Dict[str, int]], Dict[str, Dict]]:
        raw_balance_changes = defaultdict(lambda: defaultdict(int))
        extracted_metadata = {}
        balance_diff_entries = macro_data.get('balance_diff', []) or []
        for bd in balance_diff_entries:
            addr = (bd.get('address') or '').lower()
            if not addr: continue
            delta = cls.safe_int(bd.get('dirty')) - cls.safe_int(bd.get('original'))
            if delta != 0:
                raw_balance_changes[addr][NATIVE_TOKEN_ADDRESS] += delta

        processed_tokens = set()
        for asset in macro_data.get('asset_changes', []) or []:
            token_info = asset.get('token_info', {}) or {}
            token_addr = (token_info.get('contract_address') or '').lower()
            token_symbol = token_info.get('symbol')

            is_native = (
                    token_addr == '0x' + '0' * 40 or
                    not token_addr or
                    token_symbol == 'ETH'
            )

            if is_native:
                continue

            from_addr = (asset.get('from') or '').lower()
            to_addr = (asset.get('to') or '').lower()
            amount = cls.safe_int(asset.get('raw_amount'))

            if amount <= 0: continue

            if token_addr not in extracted_metadata:
                extracted_metadata[token_addr] = {
                    "symbol": token_info.get("symbol", "UNKNOWN"),
                    "name": token_info.get("name", "Unknown Token"),
                    "decimals": token_info.get("decimals", 18),
                    "contract": token_addr
                }

            if from_addr and from_addr != '0x' + '0' * 40:
                raw_balance_changes[from_addr][token_addr] -= amount
            if to_addr and to_addr != '0x' + '0' * 40:
                raw_balance_changes[to_addr][token_addr] += amount
            processed_tokens.add(token_addr)

        for log in macro_data.get('logs', []) or []:
            topics = log.get('raw', {}).get('topics', [])

            if not topics or topics[0].lower() != ERC20_TRANSFER_TOPIC:
                continue

            token_addr = log['raw']['address'].lower()
            if token_addr in processed_tokens:
                continue

            inputs = log.get('inputs', []) or []
            if len(inputs) >= 3:
                f_addr = parse_address(inputs[0].get('value') or '').lower()
                t_addr = parse_address(inputs[1].get('value') or '').lower()
                amt = cls.safe_int(inputs[2].get('value'))
            elif len(topics) >= 3:
                f_addr = parse_address(topics[1]).lower()
                t_addr = parse_address(topics[2]).lower()
                amt = hex_to_int(log['raw'].get('data', '0x0'))
            else:
                continue

            if amt > 0:
                raw_balance_changes[f_addr][token_addr] -= amt
                raw_balance_changes[t_addr][token_addr] += amt

        return raw_balance_changes, extracted_metadata

    @staticmethod
    def calculate_usd_values(
            raw_changes: Dict[str, Dict[str, int]],
            metadata: Dict[str, Dict],
            prices: Dict[str, float],
            _platform: str
    ) -> Dict[str, Dict[str, Any]]:
        final_output = {}

        chain_slug = platform_to_chain.get(_platform, 'ethereum')
        chain_config = CHAIN_CONFIG.get(chain_slug, CHAIN_CONFIG['ethereum'])
        native_symbol = chain_config['symbol']
        native_name = chain_config['name']

        for user_address, assets in raw_changes.items():
            if not user_address or user_address == '0x' + '0' * 40: continue

            user_assets = {}
            for asset_key, raw_amount in assets.items():
                if raw_amount == 0: continue

                asset_key_lower = asset_key.lower()
                price = prices.get(asset_key_lower, None)

                if asset_key_lower == NATIVE_TOKEN_ADDRESS:
                    decimals = 18
                    fmt_val = float(Decimal(raw_amount) / Decimal(10 ** decimals))
                    asset_info = {
                        "symbol": native_symbol,
                        "name": native_name,
                        "decimals": decimals,
                        "raw_amount": raw_amount,
                        "fmt_amount": format_amount(raw_amount, 18),
                        "usd_value": fmt_val * price if price else None,
                        "is_token": False,
                        "contract": "N/A"
                    }
                else:
                    meta = metadata.get(asset_key_lower, {})
                    decimals = meta.get('decimals', 18)
                    fmt_val = float(Decimal(raw_amount) / Decimal(10 ** decimals))
                    asset_info = {
                        "symbol": meta.get('symbol', 'UNKNOWN'),
                        "name": meta.get('name', 'Unknown Token'),
                        "decimals": int(decimals),
                        "raw_amount": raw_amount,
                        "fmt_amount": format_amount(raw_amount, int(decimals)),
                        "usd_value": fmt_val * price if price else None,
                        "is_token": True,
                        "contract": asset_key
                    }
                user_assets[asset_key] = asset_info

            if user_assets:
                final_output[user_address] = user_assets

        return final_output

    @staticmethod
    def calculate_usd_values_markdown(raw_changes: Dict[str, Dict[str, int]],
                                      metadata: Dict[str, Dict],
                                      prices: Dict[str, float]) -> str:
        lines = ["\n### Balance Changes (Simulation Result)"]

        has_changes = False
        for user_addr, assets in raw_changes.items():
            user_lines = []
            for token_addr, raw_amount in assets.items():
                if raw_amount == 0: continue

                meta = metadata.get(token_addr, {})
                decimals = meta.get('decimals', 18)
                symbol = meta.get('symbol', 'UNKNOWN')

                fmt_amount = float(Decimal(raw_amount) / Decimal(10 ** decimals))
                price = prices.get(token_addr, None)
                usd_value = fmt_amount * price if price else None

                sign = "+" if fmt_amount > 0 else ""
                user_lines.append(f"    - {symbol}: {sign}{fmt_amount:.4f} (${usd_value if usd_value else 'Unknown'})")

            if user_lines:
                has_changes = True
                lines.append(f"- Address: {user_addr}")
                lines.extend(user_lines)

        if not has_changes:
            return "No significant balance changes detected."

        return "\n".join(lines)


async def fetch_token_prices(
        chain_slug: str,
        timestamp: int,
        token_addresses: Set[str]
) -> Dict[str, float]:
    if not token_addresses or not timestamp:
        return {}

    chain_config = CHAIN_CONFIG.get(chain_slug, CHAIN_CONFIG['ethereum'])
    wrapped_address = chain_config['wrapped'].lower()

    final_query_list = []

    for addr in token_addresses:
        addr_lower = addr.lower()
        target = wrapped_address if addr_lower == NATIVE_TOKEN_ADDRESS else addr_lower
        final_query_list.append(f"{chain_slug}:{target}")

    if not final_query_list:
        return {}

    final_query_str = ",".join(set(final_query_list))
    price_results = {}

    try:
        prices = await get_token_price(final_query_str, timestamp)

        for key, info in prices.items():
            token_addr = key.split(':')[-1].lower()
            price = info.get('price', 0.0)

            price_results[token_addr] = price

            if token_addr == wrapped_address.lower():
                price_results[NATIVE_TOKEN_ADDRESS] = price

    except Exception as e:
        print(f"Batch price fetch error: {e}")

    return price_results
