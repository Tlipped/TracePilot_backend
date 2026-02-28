import os

import re

RAW_JSON_PATH = 'dataset/raw'

PROJECT_PATH = os.path.dirname(os.path.abspath(__file__))
# PROJECT_PATH, _ = os.path.split(os.path.realpath(__file__))
PC_TRACER_RELATED_PATH = 'misc/tracer.js'
with open(os.path.join(PROJECT_PATH, PC_TRACER_RELATED_PATH), 'r') as f:
    PC_TRACER = f.read()
    PC_TRACER = PC_TRACER[9:]
    PC_TRACER = re.sub('\n|\t', '', PC_TRACER)

PHALCON_TRACER_RELATED_PATH = 'misc/phalcon_tracer.js'
with open(os.path.join(PROJECT_PATH, PHALCON_TRACER_RELATED_PATH), 'r', encoding='utf-8') as f:
    PHALCON_TRACER = f.read()

TYPED_AST_CODE_RELATED_PATH = 'misc/ast.js'
with open(os.path.join(PROJECT_PATH, TYPED_AST_CODE_RELATED_PATH), 'r') as f:
    TYPED_AST_CODE = f.read()

SOLCJS_CODE_RELATED_PATH = 'misc/solcjs.js'
with open(os.path.join(PROJECT_PATH, SOLCJS_CODE_RELATED_PATH), 'r') as f:
    SOLCJS_CODE = f.read()

CACHE_DIR = os.path.join(PROJECT_PATH, 'data/cache')

TMP_FILE_DIR = os.path.join(PROJECT_PATH, 'tmp')
if not os.path.exists(TMP_FILE_DIR):
    os.makedirs(TMP_FILE_DIR)

NODE_PATH = 'node'

SIGNATURE_PATH = os.path.join(PROJECT_PATH, "misc/SignItem.csv")

SCAN_APIKEYS = {
    'Ethereum': [
        'https://api.etherscan.io/v2/api?chainid=1&apikey=4QP6SY9V7XXDJDEJBGC1NAI3KXRIU5CDEE',
        'https://api.etherscan.io/v2/api?chainid=1&apikey=Y3HRQPY2RXG6CQNJSV7QJXDZ1S2SCKTVTD',
        'https://api.etherscan.io/v2/api?chainid=1&apikey=XIUCSG7G93C5X7QNEZICXBWRS9SKUZY7KW',
        'https://api.etherscan.io/v2/api?chainid=1&apikey=3RDEEN6RNS694NQTCTENUKQPI7FY147KIA',
        'https://api.etherscan.io/v2/api?chainid=1&apikey=AZI8P3UZBWIFY93UKDHUPN8QC9MVH26N2E',
        'https://api.etherscan.io/v2/api?chainid=1&apikey=9DHD6VRXGI5747TFE6D8F2RAIY79ZST16I',
        'https://api.etherscan.io/v2/api?chainid=1&apikey=VUX7GADFQWS3ZY667FIBU8M7U8PGYPQ42J',
        'https://api.etherscan.io/v2/api?chainid=1&apikey=C7JHK7ZBEQJWMKC6AYTRUIMIJMMIFAF7K3',
        'https://api.etherscan.io/v2/api?chainid=1&apikey=1S6HVVBYA9A1TH4UJ1HFPH5MXAATIZT3ER',
        'https://api.etherscan.io/v2/api?chainid=1&apikey=64GTQD9HUSDKACZQ1H4AAPYWWF4GZUYA7I',
        'https://api.etherscan.io/v2/api?chainid=1&apikey=XA7QR77WE59MEFSSZJ81QB6RCA9YKE8TYJ',
        'https://api.etherscan.io/v2/api?chainid=1&apikey=H2WU5N2A1CQFCBA5N6MHJ7V4HWE3QK4VHS',
        'https://api.etherscan.io/v2/api?chainid=1&apikey=KC2IM55M9UR13FC2JWW86EGY2SR3XNRWIA',
        'https://api.etherscan.io/v2/api?chainid=1&apikey=32D4QMS1KB3XGEG1YG99MEBQX9XUEUSBNC',
        'https://api.etherscan.io/v2/api?chainid=1&apikey=MCDE2ZV3GVKQBRSJ5V4VE82NHT32NM4A2Z',
        'https://api.etherscan.io/v2/api?chainid=1&apikey=99B5J7A8RGP7ZQDSHSMN428NMS59J4DD51',
        'https://api.etherscan.io/v2/api?chainid=1&apikey=1AHNVWUGE9K6HYHVHQIG5DUCHP7AM421GQ',
        'https://api.etherscan.io/v2/api?chainid=1&apikey=HBV2JXKQ9P4BGWGCB7DJB5GJIVRB45N3UR',
        'https://api.etherscan.io/v2/api?chainid=1&apikey=TE92WJABYJZV7IKEQGSE7PYPHYUZ8TVX2F',
        'https://api.etherscan.io/v2/api?chainid=1&apikey=4JX4TB3WQTJH3J4D182NAFEZS9W19MAIZ9'
    ],
    'BNBChain': [
        'https://api.etherscan.io/v2/api?chainid=56&apikey=4QP6SY9V7XXDJDEJBGC1NAI3KXRIU5CDEE',
        'https://api.etherscan.io/v2/api?chainid=56&apikey=Y3HRQPY2RXG6CQNJSV7QJXDZ1S2SCKTVTD',
        'https://api.etherscan.io/v2/api?chainid=56&apikey=XIUCSG7G93C5X7QNEZICXBWRS9SKUZY7KW',
        'https://api.etherscan.io/v2/api?chainid=56&apikey=3RDEEN6RNS694NQTCTENUKQPI7FY147KIA',
        'https://api.etherscan.io/v2/api?chainid=56&apikey=AZI8P3UZBWIFY93UKDHUPN8QC9MVH26N2E',
        'https://api.etherscan.io/v2/api?chainid=56&apikey=9DHD6VRXGI5747TFE6D8F2RAIY79ZST16I',
        'https://api.etherscan.io/v2/api?chainid=56&apikey=VUX7GADFQWS3ZY667FIBU8M7U8PGYPQ42J',
        'https://api.etherscan.io/v2/api?chainid=56&apikey=C7JHK7ZBEQJWMKC6AYTRUIMIJMMIFAF7K3',
        'https://api.etherscan.io/v2/api?chainid=56&apikey=1S6HVVBYA9A1TH4UJ1HFPH5MXAATIZT3ER',
        'https://api.etherscan.io/v2/api?chainid=56&apikey=64GTQD9HUSDKACZQ1H4AAPYWWF4GZUYA7I',
        'https://api.etherscan.io/v2/api?chainid=56&apikey=XA7QR77WE59MEFSSZJ81QB6RCA9YKE8TYJ',
        'https://api.etherscan.io/v2/api?chainid=56&apikey=H2WU5N2A1CQFCBA5N6MHJ7V4HWE3QK4VHS',
        'https://api.etherscan.io/v2/api?chainid=56&apikey=KC2IM55M9UR13FC2JWW86EGY2SR3XNRWIA',
        'https://api.etherscan.io/v2/api?chainid=56&apikey=32D4QMS1KB3XGEG1YG99MEBQX9XUEUSBNC',
        'https://api.etherscan.io/v2/api?chainid=56&apikey=MCDE2ZV3GVKQBRSJ5V4VE82NHT32NM4A2Z',
        'https://api.etherscan.io/v2/api?chainid=56&apikey=99B5J7A8RGP7ZQDSHSMN428NMS59J4DD51',
        'https://api.etherscan.io/v2/api?chainid=56&apikey=1AHNVWUGE9K6HYHVHQIG5DUCHP7AM421GQ',
        'https://api.etherscan.io/v2/api?chainid=56&apikey=HBV2JXKQ9P4BGWGCB7DJB5GJIVRB45N3UR',
        'https://api.etherscan.io/v2/api?chainid=56&apikey=TE92WJABYJZV7IKEQGSE7PYPHYUZ8TVX2F',
        'https://api.etherscan.io/v2/api?chainid=56&apikey=4JX4TB3WQTJH3J4D182NAFEZS9W19MAIZ9'
    ]
}

JSONRPCS = {
    'Ethereum': [
        'https://mainnet.chainnodes.org/965e82de-fa68-404c-82b9-6f078bdb3c30',
        'https://mainnet.chainnodes.org/112ae60a-a46d-45a2-9e2e-322ca16d9ce4',
        'https://mainnet.chainnodes.org/c4cb0bc7-4b66-4b17-8429-ecef107ef315',
        'https://mainnet.chainnodes.org/9243a4a3-42c3-4ce0-b379-d505cd1e1d46',
        'https://mainnet.chainnodes.org/c2db8f3e-fd09-44d4-be0e-537b43dfb804',

    ],
    'BNBChain': [
        'https://bsc-mainnet.chainnodes.org/8da88aa0-51c6-46e3-bf65-328959e913d4',
        'https://bsc-mainnet.chainnodes.org/fd7f1a79-ab58-4ab8-aa59-428c1428f2c7',
        'https://bsc-mainnet.chainnodes.org/9813f9d1-f1bb-43cc-a9f4-2b89fd98cc70',
        'https://bsc-mainnet.chainnodes.org/5b9deb84-b014-4f43-8f65-7fe5c856b04e',
        'https://bsc-mainnet.chainnodes.org/374cd51d-fa26-44c3-ba36-75ac7ba1587b'
    ]
}

WEB3_PROVIDER = "https://mainnet.infura.io/v3/1277d00f1f424cbb9212838bb034a8ce"

LLM_NAME = 'deepseek-reasoner'
LLM_API_KEY = 'sk-3b88436dd2044fc6a9d3fdccb779b1c6'
LLM_BASE_URL = 'https://api.deepseek.com'
LLM_MAX_CONCURRENT = 5

PROMPT_PATH = 'prompt'

INIT_DEPTH = 2

MANIPULATE_PATH = os.path.join(PROJECT_PATH, "local_manipulate")
PATCH_FILE = "test/PatchVerification.t.sol"
ATTACK_DATA_FILE = "test/data/attack_data.json"
MCP_SERVER_PATH = os.path.join(PROJECT_PATH, "mcp_tools/mcp_server.py")

TOTAL_TURN = 3
GUIDE_TURN = 15
TX_DEBUG_TURN = 80
TX_DEBUG_MAX_IDLE_TURNS = 40
PATCH_TURN = 3
FIX_TURN = 15

# Tenderly API settings
TENDERLY_API_KEY = "ODB9NAdtxqcOaaDcFdywo3eZCZy3yE6Y"
ACCOUNT_SLUG = "Feiqiua"
PROJECT_SLUG = "project"

PLATFORM_TO_CHAIN_ID = {
    "Ethereum": 1,
    "BNBChain": 56,
    "BSC": 56,
    "Arbitrum": 42161,
    "Polygon": 137,
    "Optimism": 10,
    "Avalanche": 43114,
    "Fantom": 250,
    "Base": 8453
}

TRANSACTION_FILTER_SMALL_SET_THRESHOLD = 1
