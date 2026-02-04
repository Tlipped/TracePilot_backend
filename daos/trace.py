from typing import List, Dict, Any, Optional

from downloaders.defs import Downloader
from downloaders.trace import FlatTraceDownloader
from utils.web3 import hex_to_dec


class PCTraceItem:
    def __init__(
            self, transaction_hash: str, index: int, pc: int,
            opcode: str, depth: int, address: str, is_error: bool, stack: List[str] = None, memory: str = None, memory_full_len=0
    ):
        self.transaction_hash = transaction_hash
        self.index = index
        self.pc = pc
        self.opcode = opcode
        self.depth = depth
        self.address = address
        self.is_error = is_error
        self.stack = stack if stack else []
        self.memory = memory if memory else "0x"
        self.memory_full_len = memory_full_len

    def to_dict(self) -> Dict[str, Any]:
        return {
            "transaction_hash": self.transaction_hash,
            "index": self.index,
            "pc": self.pc,
            "opcode": self.opcode,
            "depth": self.depth,
            "address": self.address,
            "is_error": self.is_error,
            "stack": self.stack,
            "memory": self.memory,
            "memory_full_len": self.memory_full_len
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PCTraceItem':
        return cls(
            transaction_hash=data.get("transaction_hash", ""),
            index=data.get("index", 0),
            pc=data.get("pc", -1),
            opcode=data.get("opcode", ""),
            depth=data.get("depth", 0),
            address=data.get("address", ""),
            is_error=data.get("is_error", False),
            stack=data.get("stack", []),
            memory=data.get("memory", "0x"),
            memory_full_len=data.get("memory_full_len", 0)
        )

    def __str__(self):
        return '{} {} {}({})'.format(self.transaction_hash, self.address, self.pc, self.opcode)


class TraceItem:
    def __init__(
            self, transaction_hash: str, index: int,
            address_from: str, address_to: str,
            ctype: str, value: int,
            gas: int, gas_used: int,
            data_input: str,
    ):
        self.transaction_hash = transaction_hash
        self.index = index
        self.address_from = address_from
        self.address_to = address_to
        self.ctype = ctype
        self.value = value
        self.gas_used = gas_used
        self.gas = gas
        self.data_input = data_input

    def __str__(self):
        return '{}_{}: {}->{} {} {} {}'.format(
            self.transaction_hash, self.index,
            self.address_from, self.address_to,
            self.ctype, self.value, self.data_input
        )


class PCTraceDao:
    def __init__(self, downloader: Downloader):
        self.downloader = downloader

    async def get_pc_list(self, transaction_hash: str) -> List[PCTraceItem]:
        """
        Generate a series of `PCTraceItem` from RPC, with fields as
        `pc`, `op`, `depth`, and `address`.

        :param transaction_hash: hash of the specific transaction.
        :return: A generator.
        """
        items = await self.downloader.download(transaction_hash=transaction_hash)
        return [PCTraceItem(
            transaction_hash=transaction_hash,
            index=i,
            pc=int(item.get('pc', -1)),
            opcode=item.get('op', ''),
            depth=int(item.get('depth', -1)),
            address=item.get('address', ''),
            is_error=item.get('is_error', False),
        ) for i, item in enumerate(items)]


class TraceDao:
    def __init__(self, downloader: Downloader):
        self.downloader = downloader

    async def get_call_list(self, transaction_hash: str) -> List[TraceItem]:
        """
        Generate a series of `PCTraceItem` from RPC, with fields as
        `pc`, `op`, `depth`, and `address`.

        :param transaction_hash: hash of the specific transaction.
        :return: A generator.
        """
        items = await self.downloader.download(transaction_hash=transaction_hash)
        return [TraceItem(
            transaction_hash=transaction_hash,
            index=i,
            address_from=item.get('from', ''),
            address_to=item.get('to', ''),
            ctype=item.get('type', ''),
            value=hex_to_dec(item.get('value', '0x0')),
            gas=hex_to_dec(item.get('gas', '0x0')),
            gas_used=hex_to_dec(item.get('gasUsed', '0x0')),
            data_input=item.get('input', ''),
        ) for i, item in enumerate(items)]

    async def get_call_tree(self, transaction_hash: str):
        items = await self.downloader.download(transaction_hash=transaction_hash)
        return items


async def test():
    from downloaders.trace import PCTraceDownloader

    txhash = '0x2355e889be00edc00da421ba10b447aa2c3a539bf23ad34935b477889401ffd2'
    downloader = PCTraceDownloader('https://mainnet.chainnodes.org/112ae60a-a46d-45a2-9e2e-322ca16d9ce4')
    pc_list = await PCTraceDao(downloader=downloader).get_pc_list(transaction_hash=txhash)
    for item in pc_list:
        print(item.pc, item.opcode, item.depth, item.address, item.is_error)

    downloader = FlatTraceDownloader('https://mainnet.chainnodes.org/112ae60a-a46d-45a2-9e2e-322ca16d9ce4')
    trace_list = await TraceDao(downloader=downloader).get_call_list(transaction_hash=txhash)
    for item in trace_list:
        print(item)

if __name__ == '__main__':
    import asyncio

    asyncio.run(test())
