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
        'https://api.etherscan.io/v2/api?chainid=1&apikey=xxx',
        'https://api.etherscan.io/v2/api?chainid=1&apikey=xxx'
    ],
    'BNBChain': [
        'https://api.etherscan.io/v2/api?chainid=56&apikey=xxx',
        'https://api.etherscan.io/v2/api?chainid=56&apikey=xxx'
    ]
}

JSONRPCS = {
    'Ethereum': [
        'https://mainnet.chainnodes.org/xxx',
        'https://mainnet.chainnodes.org/xxx'
    ],
    'BNBChain': [
        'https://bsc-mainnet.chainnodes.org/xxx',
        'https://bsc-mainnet.chainnodes.org/xxx',
    ]
}

WEB3_PROVIDER = "https://mainnet.infura.io/v3/xxx"

LLM_NAME = ''
LLM_API_KEY = ''
LLM_BASE_URL = ''
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
TENDERLY_API_KEY = ""
ACCOUNT_SLUG = ""
PROJECT_SLUG = ""

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
