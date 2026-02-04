import asyncio
import json
import os

import aiohttp
from typing import Dict, Union
from settings import CACHE_DIR

HTTP_PROXY = None
platform_to_chain = {
    "Ethereum": "ethereum",
    "BNBChain": "bsc"
}


def load_from_file(filepath: str) -> Union[Dict, None]:
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error occurred while reading the cache file (requesting again): {filepath}, Error: {e}")
            return None
    return None


def save_to_file(filepath: str, data: Dict):
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
            print(f"Data saved in: {filepath}")

    except Exception as e:
        print(f"Write cache error: {filepath}, error: {e}")


async def get_single_token_price(
        chain: str,
        address: str,
        timestamp: int,
        search_width: str = '4h'
) -> Dict:

    filename = f"{chain}_{address}_{timestamp}.json"
    filepath = os.path.join(CACHE_DIR, 'price', filename)
    cached_data = load_from_file(filepath)

    if cached_data:
        return cached_data

    # print(f"Fetching API: {chain}:{address} at {timestamp}...")
    coin_key = f"{chain}:{address}"
    url = f'https://coins.llama.fi/prices/historical/{timestamp}/{coin_key}?searchWidth={search_width}'

    proxy = None
    async with aiohttp.ClientSession() as client:
        try:
            async with client.get(url, headers={'Accept': 'application/json'}, proxy=proxy) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'coins' in data and coin_key in data['coins']:
                        info = data['coins'][coin_key]

                        result = {
                            "contract_address": address,
                            "chain": chain,
                            "timestamp": timestamp,
                            "price": float(info.get('price', 0)),
                            "symbol": info.get('symbol', 'Unknown'),
                            "decimals": int(info.get('decimals', 0)),
                            "confidence": info.get('confidence', 0)
                        }

                        save_to_file(filepath, result)
                        return result
                    else:
                        return {}
        except Exception as e:
            print(f"Network Error: {e}")
            return {}
    return {}


async def get_token_price(coins: str, timestamp: int) -> Dict:
    """
    coins : "chain:address,chain:address"
    """
    result = {}
    tasks = []
    coin_list = coins.split(',')

    for coin in coin_list:
        if ':' in coin:
            chain, address = coin.split(':')
            tasks.append(get_single_token_price(chain, address, timestamp))

    items = await asyncio.gather(*tasks)

    for coin, item in zip(coin_list, items):
        if item:
            result[coin] = item
    return result


if __name__ == "__main__":
    test_coins = "ethereum:0x9a13867048e01c663ce8ce2fe0cdae69ff9f35e3"
    test_ts = 1698000000

    loop = asyncio.get_event_loop()
    prices = loop.run_until_complete(get_token_price(test_coins, test_ts))

    print(json.dumps(prices, indent=2))
