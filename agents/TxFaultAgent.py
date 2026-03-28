import os
from typing import Dict

from agents.AgentBase import AgentBase
from prompt.bug_summary_prompt import BUG_SUMMARY_SP, BUG_SUMMARY_UP
from settings import CACHE_DIR


class TxFaultAgent(AgentBase):
    def __init__(self, dapp_name, name="TxFaultAgent", log_callback=None):
        super(TxFaultAgent, self).__init__(name, BUG_SUMMARY_SP, unique_id=dapp_name,log_callback=log_callback)

    async def handle(self, dapp_data: Dict) -> str:
        bug_path = os.path.join(CACHE_DIR, 'summary/bug_summary', '%s.json' % dapp_data.get("dapp").get("name"))
        if os.path.exists(bug_path):
            bug_summary = self.load_summary_from_cache(str(bug_path))
        else:
            bug_summary = await self.query(BUG_SUMMARY_UP.format(
                tx_hash=dapp_data.get("transaction_hash_list", ""),
                tx_detail=dapp_data.get("transaction_detail", ""),
                transfer_graph=dapp_data.get("transfer_graph", ""),
                trace_tree=dapp_data.get("trace_tree", ""),
                tx_token_property=dapp_data.get("transaction_to_property", ""),
                attack_list=dapp_data.get("attack_transactions", ""),
                auxiliary_list=dapp_data.get("auxiliary_transactions", ""),
                tx_roles=dapp_data.get("transaction_roles", ""),
            ), _format='str')
            # write cache
            self.write_summary_to_cache(cache_path='summary/bug_summary', file_name=dapp_data.get("dapp").get("name"),
                                        summary=bug_summary)
        return bug_summary
