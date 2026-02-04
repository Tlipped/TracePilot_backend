from typing import List, Dict

import networkx as nx
from decimal import Decimal
from utils.price import get_token_price, platform_to_chain


class FundFlowGraphBuilder:
    def __init__(self, platform: str):
        self.graph = nx.MultiDiGraph()
        self.native_symbol = "native"
        self.platform = platform
        self.NATIVE_TOKEN_ADDRESS = '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'
        self.processed_txs = set()

    def _normalize_amount(self, change):
        token_info = change.get("token_info", {})
        decimals = token_info.get("decimals", 18)
        amount = change.get("amount")

        if amount is not None:
            return Decimal(str(amount))

        raw_amount = change.get("raw_amount")
        if raw_amount is not None:
            val = int(raw_amount, 16) if str(raw_amount).startswith("0x") else int(raw_amount)
            return Decimal(val) / Decimal(10 ** decimals)

        return Decimal("0")

    async def _fetch_all_historical_prices(self, transactions: List[Dict]):
        price_cache = {}  # (timestamp, address) -> price

        for tx in transactions:
            ts = tx.get("timestamp")
            contracts = set()

            for change in tx.get("asset_changes", []):
                addr = change.get("token_info", {}).get("contract_address")
                if addr:
                    contracts.add(addr.lower())

            if tx.get("balance_diff"):
                contracts.add(self.NATIVE_TOKEN_ADDRESS.lower())

            if not contracts:
                continue

            chain = platform_to_chain.get(self.platform, "")
            coins_query = ",".join([f"{chain}:{addr}" for addr in contracts])

            try:
                prices = await get_token_price(coins_query, ts)
                for _key, info in prices.items():
                    addr = _key.split(':')[-1].lower()
                    price_cache[(ts, addr)] = info.get('price', 0.0)
            except Exception as e:
                print(f"Error fetching price for ts {ts}: {e}")

        return price_cache

    async def add_transactions(self, transactions: List[Dict]):
        sorted_txs = sorted(
            transactions,
            key=lambda x: (x.get("block_number", 0), x.get("transaction_index", 0))
        )

        price_map = await self._fetch_all_historical_prices(sorted_txs)

        for tx in sorted_txs:
            tx_hash = tx.get("tx_hash", "unknown_tx")
            if tx_hash in self.processed_txs:
                continue

            ts = tx.get("timestamp")
            asset_changes = tx.get("asset_changes", [])
            balance_diff = tx.get("balance_diff", [])

            for idx, change in enumerate(asset_changes):
                token_info = change.get("token_info", {})
                change_type = change.get("type", "Transfer")
                from_addr = change.get("from")
                to_addr = change.get("to")

                if change_type.lower() == "mint" and not from_addr:
                    from_addr = "0x0000000000000000000000000000000000000000"
                if change_type.lower() == "burn" and not to_addr:
                    to_addr = "0x0000000000000000000000000000000000000000"

                if not from_addr or not to_addr:
                    continue

                contract_address = token_info.get("contract_address", "Unknown").lower()
                amount = self._normalize_amount(change)
                raw_amount = change.get("raw_amount", "")
                price = price_map.get((ts, contract_address), 0.0)
                usd_value = float(amount) * price

                edge_key = f"{tx_hash}_{change_type}_{idx}"

                self.graph.add_edge(
                    from_addr, to_addr,
                    edge_key=edge_key,
                    amount=float(amount),
                    raw_amount=raw_amount,
                    symbol=token_info.get("symbol") or token_info.get("name", "Unknown"),
                    token_address=contract_address,
                    dollar_value=usd_value,
                    timestamp=ts
                )

            native_price = price_map.get((ts, self.NATIVE_TOKEN_ADDRESS.lower()), 0.0)

            for idx, diff in enumerate(balance_diff):
                addr = diff.get("address")
                delta = int(diff.get("dirty", 0)) - int(diff.get("original", 0))
                if delta == 0:
                    continue

                amount = abs(Decimal(delta) / Decimal(10 ** 18))
                usd_value = float(amount) * native_price

                if delta < 0:
                    to_addr = "0xMINER" if diff.get("is_miner") else "0xOUT_GAS_OR_OTHER"
                    u, v = addr, to_addr
                    edge_type = "Native_Out"
                else:
                    from_addr = "0xMINER" if diff.get("is_miner") else "0xSOURCE"
                    u, v = from_addr, addr
                    edge_type = "Native_In"

                edge_key = f"{tx_hash}_{edge_type}_{idx}"

                self.graph.add_edge(
                    u, v,
                    edge_key=edge_key,
                    amount=float(amount),
                    symbol=self.native_symbol,
                    dollar_value=usd_value,
                    timestamp=ts
                )

            self.processed_txs.add(tx_hash)

        return self.graph
