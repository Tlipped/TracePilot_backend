import logging
from typing import Dict, Any, List, Optional, Union
from datetime import datetime
from web3 import Web3
from utils.log import LogAnalyzer


class TxDetailItem:
    def __init__(self, raw_data: Dict[str, Any], log_analyzer: Optional[LogAnalyzer] = None):
        self.raw_data = raw_data if raw_data else {}
        self.tx_core = self.raw_data.get("transaction", {})
        self.tx_info = self.raw_data.get("transaction", {}).get("transaction_info", {})

        if not self.tx_core and "hash" in self.raw_data:
            self.tx_core = self.raw_data
            self.tx_info = self.raw_data.get("transaction_info", {})

        self.log_analyzer = log_analyzer

        self._parse_core_data()
        self._analyze_features()

    def _safe_int(self, value: Union[str, int, None]) -> int:
        if value is None:
            return 0
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            if value.startswith("0x"):
                return int(value, 16) if value != "0x" else 0
            try:
                return int(value)
            except ValueError:
                return 0
        return 0

    def _safe_address(self, addr: Any) -> Optional[str]:
        if not addr or not isinstance(addr, str):
            return None
        try:
            return Web3.to_checksum_address(addr)
        except Exception:
            return addr

    def _parse_timestamp(self, ts_str: str) -> int:
        if not ts_str:
            return 0
        try:
            dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
            return int(dt.timestamp())
        except Exception as e:
            print(f"Failed to parse timestamp {ts_str}: {e}")
            return 0

    def _parse_core_data(self):
        self.hash = self.tx_core.get("hash")
        self.block_number = self._safe_int(self.tx_core.get("block_number"))
        self.block_hash = self.tx_core.get("block_hash", "")
        self.network_id = self.tx_core.get("network_id", "1")
        self.timestamp_str = self.tx_core.get("timestamp", "")
        self.timestamp = self._parse_timestamp(self.timestamp_str)

        self.from_address = self._safe_address(self.tx_core.get("from"))
        self.to_address = self._safe_address(self.tx_core.get("to"))

        self.value = self._safe_int(self.tx_core.get("value"))
        self.input_data = self.tx_core.get("input", "0x")
        self.nonce = self._safe_int(self.tx_core.get("nonce"))
        self.transaction_index = self._safe_int(self.tx_core.get("index"))

        self.gas_limit = self._safe_int(self.tx_core.get("gas"))
        self.gas_price = self._safe_int(self.tx_core.get("gas_price"))
        self.gas_used = self._safe_int(self.tx_core.get("gas_used", self.tx_info.get("gas_used", 0)))

        self.cumulative_gas_used = self._safe_int(self.tx_core.get("cumulative_gas_used"))
        self.effective_gas_price = self.gas_price

        self.interacted_addresses = self.tx_core.get("addresses")
        self.contract_ids = self.tx_core.get("contract_ids")

        raw_status = self.tx_core.get("status")
        if isinstance(raw_status, bool):
            self.status = 1 if raw_status else 0
        else:
            self.status = self._safe_int(raw_status)
        self.is_success = (self.status == 1) or (raw_status is True)

        self.logs = self.tx_info.get("logs", []) or []
        self.contract_address = self._safe_address(self.tx_info.get("contract_address"))

        self.state_diff = self.tx_info.get("state_diff", []) or []
        self.balance_diff = self.tx_info.get("balance_diff", []) or []
        self.balance_changes = self.tx_info.get('balance_changes', []) or []
        self.asset_changes = self.tx_info.get('asset_changes', []) or []

    def _analyze_features(self):
        self.transaction_type = self._classify_transaction()

        self.total_cost_wei = self.gas_used * self.gas_price
        self.total_cost_eth = float(Web3.from_wei(self.total_cost_wei, 'ether'))
        self.value_eth = float(Web3.from_wei(self.value, 'ether'))

        if self.gas_limit > 0:
            self.gas_used_percentage = (self.gas_used / self.gas_limit) * 100
        else:
            self.gas_used_percentage = 0
        self.gas_waste_percentage = 100 - self.gas_used_percentage if self.gas_used_percentage > 0 else 0

        self.is_contract_interaction = self.input_data != '0x' and len(self.input_data) > 2
        self.function_signature = self.input_data[:10] if self.is_contract_interaction and len(
            self.input_data) >= 10 else None

        self.transfer_events = self._extract_transfer_events()
        self.other_events = self._extract_other_events()

        self.detailed_logs = []
        if self.log_analyzer:
            self.detailed_logs = [self.log_analyzer.process_log(log) for log in self.logs]

    def _classify_transaction(self) -> str:
        zero_addr = "0x0000000000000000000000000000000000000000"
        if (self.to_address is None) or (self.to_address == zero_addr and self.from_address != zero_addr):
            return "contract_creation"
        if self.input_data != '0x' and len(self.input_data) > 2:
            return "contract_call"
        if self.value > 0 and self.input_data == '0x':
            return "eth_transfer"
        if self._has_transfer_events():
            return "token_transfer"
        return "other"

    def _has_transfer_events(self) -> bool:
        transfer_topic = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
        for log in self.logs:
            topics = log.get('topics', [])
            if topics and topics[0] == transfer_topic:
                return True
        return False

    def _extract_transfer_events(self) -> List[Dict[str, Any]]:
        events = []
        transfer_topic = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'

        for log in self.logs:
            topics = log.get('topics', [])
            if not topics or topics[0] != transfer_topic:
                continue
            parsed = {
                'address': self._safe_address(log.get('address')),
                'from': '0x' + topics[1][26:] if len(topics) > 1 else None,
                'to': '0x' + topics[2][26:] if len(topics) > 2 else None,
                'value': self._safe_int(log.get('data', '0')),
                'log_index': self._safe_int(log.get('logIndex') or log.get('index')),
                'block_number': self.block_number
            }
            events.append(parsed)
        return events

    def _extract_other_events(self) -> List[Dict[str, Any]]:
        events = []
        transfer_topic = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'

        for log in self.logs:
            topics = log.get('topics', [])
            if topics and topics[0] == transfer_topic:
                continue

            events.append({
                'address': self._safe_address(log.get('address')),
                'topics': topics,
                'data': log.get('data'),
                'log_index': self._safe_int(log.get('logIndex') or log.get('index'))
            })
        return events

    def to_dict(self) -> Dict[str, Any]:
        return {
            'hash': self.hash,
            'block_hash': self.block_hash,
            'block_number': self.block_number,
            'transaction_index': self.transaction_index,
            'timestamp': self.timestamp,
            'from': self.from_address,
            'to': self.to_address,
            'value': {
                'wei': self.value,
                'eth': self.value_eth
            },
            'input_data': self.input_data,
            'function_signature': self.function_signature,
            'nonce': self.nonce,
            'gas': {
                'limit': self.gas_limit,
                'used': self.gas_used,
                'used_percentage': round(self.gas_used_percentage, 2),
                'waste_percentage': round(self.gas_waste_percentage, 2),
                'price_wei': self.gas_price,
                'effective_price_wei': self.effective_gas_price,
                'cumulative_used': self.cumulative_gas_used
            },
            'cost': {
                'total_wei': self.total_cost_wei,
                'total_eth': self.total_cost_eth
            },
            'status': {
                'code': self.status,
                'success': self.is_success,
                'description': 'success' if self.is_success else 'failed'
            },
            'contract_address': self.contract_address,
            'type_analysis': {
                'transaction_type': self.transaction_type,
                'is_contract_creation': self.contract_address is not None,
                'is_contract_interaction': self.is_contract_interaction,
                'chain_id': self._safe_int(self.network_id),
            },
            'log_analysis': {
                'total_logs': len(self.logs),
                'transfer_events': len(self.transfer_events),
                'interacted_addresses': self.interacted_addresses,
                'detailed_logs': self.detailed_logs
            },
            'state_diff': self.state_diff,
            'balance_diff': self.balance_diff,
            'balance_changes': self.balance_changes,
            'asset_changes': self.asset_changes
        }

    def __str__(self) -> str:
        return (f"Transaction {self.hash}\n"
                f"Type: {self.transaction_type.upper()} | Block: {self.block_number} | Status: {'SUCCESS' if self.is_success else 'FAILED'}\n"
                f"From: {self.from_address} -> To: {self.to_address or 'Contract Creation'}\n"
                f"Value: {self.value_eth} ETH | Fee: {self.total_cost_eth:.6f} ETH\n"
                f"Gas: {self.gas_used}/{self.gas_limit} ({self.gas_used_percentage:.1f}% used)\n"
                f"Events: {len(self.detailed_logs)} logs, {len(self.transfer_events)} transfers, {len(self.other_events)} other events")
