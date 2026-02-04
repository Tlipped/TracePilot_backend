from typing import Dict, List, Set
import asyncio
from downloaders.defs import Downloader

from utils.token_utils import ERC20_TRANSFER_TOPIC, ERC721_TRANSFER_TOPIC, ERC1155_SINGLE_TRANSFER_TOPIC, \
    ERC1155_BATCH_TRANSFER_TOPIC, TOKEN_APPROVE_TOPIC, TOKEN_APPROVE_ALL_TOPIC


class TokenPropertyItem:
    """
    The item for transmitting token properties.
    """
    def __init__(
            self, contract_address: str, name: str, token_symbol: str,
            decimals: int, total_supply: int
    ):
        self.contract_address = contract_address
        self.name = name
        self.token_symbol = token_symbol
        self.decimals = decimals
        self.total_supply = total_supply

    def __str__(self):
        return 'contract_address:{}, {}({}), decimals:{}, total_supply:{}'.format(self.contract_address, self.name, self.token_symbol, self.decimals, self.total_supply)

    def to_dict(self) -> Dict:
        return {
            'contract_address': self.contract_address,
            'name': self.name,
            'token_symbol': self.token_symbol,
            'decimals': self.decimals,
            'total_supply': self.total_supply
        }


class TokenPropertyDao:
    def __init__(self, property_downloader: Downloader, event_log_downloader: Downloader):
        self.property_downloader = property_downloader
        self.event_log_downloader = event_log_downloader
        self._cache = {}
        self._pending_requests = {}
        self.contract_extractor = TokenContractExtractor()

    async def get_token_property_from_transaction(self, transaction_hash: str) -> List[TokenPropertyItem]:
        event_logs = await self.event_log_downloader.download(transaction_hash=transaction_hash)
        token_contracts = self.contract_extractor.extract_token_contracts(event_logs)
        contract_addresses = list(token_contracts.keys())
        property_list = []
        for contract_address in contract_addresses:
            try:
                property_item = await self.get_token_property(contract_address)
                property_list.append(property_item)
            except Exception as e:
                continue
        return property_list

    async def get_token_property(self, contract_address: str) -> TokenPropertyItem:
        if contract_address in self._cache:
            property_data = self._cache[contract_address]
            return self._create_property_item(contract_address, property_data)

        if contract_address in self._pending_requests:
            await self._pending_requests[contract_address]
            if contract_address in self._cache:
                property_data = self._cache[contract_address]
                return self._create_property_item(contract_address, property_data)

        future = asyncio.Future()
        self._pending_requests[contract_address] = future

        try:
            property_data = await self.property_downloader.download(contract_address=contract_address)
            self._cache[contract_address] = property_data
            future.set_result(property_data)
            return self._create_property_item(contract_address, property_data)

        except Exception as e:
            future.set_exception(e)
            default_data = {'name': '', 'token_symbol': '', 'decimals': -1, 'total_supply': -1}
            return self._create_property_item(contract_address, default_data)

        finally:
            self._pending_requests.pop(contract_address, None)

    def _create_property_item(self, contract_address: str, property_data: Dict) -> TokenPropertyItem:
        return TokenPropertyItem(
            contract_address=contract_address,
            name=property_data.get('name', ''),
            token_symbol=property_data.get('token_symbol', ''),
            decimals=property_data.get('decimals', -1),
            total_supply=property_data.get('total_supply', -1)
        )

    def clear_cache(self):
        self._cache.clear()
        self._pending_requests.clear()


class TokenContractExtractor:

    def __init__(self):
        self.token_event_signatures = {
            ERC20_TRANSFER_TOPIC: 'ERC20',
            ERC721_TRANSFER_TOPIC: 'ERC721',
            ERC1155_SINGLE_TRANSFER_TOPIC: 'ERC1155',
            ERC1155_BATCH_TRANSFER_TOPIC: 'ERC1155',
            TOKEN_APPROVE_TOPIC: 'ERC20/ERC721',
            TOKEN_APPROVE_ALL_TOPIC: 'ERC721/ERC1155'
        }

    def extract_token_contracts(self, event_logs: List[Dict]) -> Dict[str, Set[str]]:
        token_contracts = {}

        for log in event_logs:
            topics = log.get('topics', [])
            if not topics:
                continue
            event_signature = topics[0]
            contract_address = log.get('address', '').lower()

            if event_signature in self.token_event_signatures:
                token_type = self.token_event_signatures[event_signature]
                if contract_address not in token_contracts:
                    token_contracts[contract_address] = set()
                token_contracts[contract_address].add(token_type)
        return token_contracts
