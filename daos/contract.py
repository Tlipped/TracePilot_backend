import asyncio
import copy
import json
import os
import platform
import re
from typing import List, Dict, Union, Any

from downloaders.defs import Downloader
from entities.contract import ContractEntityForCompile
from settings import PROJECT_PATH, NODE_PATH, SOLCJS_CODE, CACHE_DIR
from utils.solc import SourceMappingItem, Solc, SolcJS
from utils.tmpfile import wrap_run4tmpfile


class ContractCompileItem:
    def __init__(
            self, contract_address: str, bytecode: str,
            ast: Dict, source_mapping: List[SourceMappingItem],
            abi: List = None, contract_name: str = "",
            errors: List[Dict] = None,
            creation_bytecode: str = "",
            immutable_references: Dict = None
    ):
        self.contract_address = contract_address
        self.bytecode = bytecode  # Runtime Bytecode
        self.creation_bytecode = creation_bytecode  # Creation Bytecode
        self.ast = ast
        self.source_mapping = source_mapping
        self.abi = abi if abi is not None else []
        self.contract_name = contract_name
        self.errors = errors if errors is not None else []
        self.immutable_references = immutable_references if immutable_references is not None else {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_address": self.contract_address,
            "bytecode": self.bytecode,
            "creation_bytecode": self.creation_bytecode,
            "ast": self.ast,
            "source_mapping": [item.to_dict() for item in self.source_mapping],
            "abi": self.abi,
            "errors": self.errors,
            "immutable_references": self.immutable_references
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ContractCompileItem':
        raw_mappings = data.get("source_mapping", [])
        mapping_objs = [SourceMappingItem.from_dict(m) for m in raw_mappings]

        return cls(
            contract_address=data.get("contract_address", ""),
            bytecode=data.get("bytecode", ""),
            creation_bytecode=data.get("creation_bytecode", ""),
            ast=data.get("ast", {}),
            source_mapping=mapping_objs,
            abi=data.get("abi", []),
            errors=data.get("errors", []),
            immutable_references=data.get("immutable_references", {})
        )

    def is_success(self) -> bool:
        """Helper to check if compilation yielded bytecode"""
        return bool(self.bytecode and len(self.bytecode) > 2)


class ContractDao:
    def __init__(self, downloader: Downloader):
        self.downloader = downloader

    async def get_contract_name(self, contract_address: str) -> str:
        # fetch contract name from source code
        result = await self.downloader.download(contract_address=contract_address)
        if isinstance(result, str) and len(result) < 5:
            file_path = os.path.join(CACHE_DIR, f"source/{contract_address}.json")
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass
                result = await self.downloader.download(contract_address=contract_address)
        if isinstance(result, dict):
            contract_name = result.get("ContractName", "")
        else:
            print(f"[WARN] Expected dict but got {type(result)} for address {contract_address}")
            # 尝试转换为字符串并处理
            result_str = str(result)
            if result_str.startswith('{'):
                try:
                    result_dict = json.loads(result_str)
                    contract_name = result_dict.get("ContractName", "")
                except:
                    contract_name = ""
            else:
                contract_name = ""
        return contract_name

    async def get_source_code(self, contract_address: str) -> Dict[str, str]:
        """
        Return the contract source code, which is fetched from etherscan.

        :param contract_address: the address of specific contract
        :return: the mapping from filename to source code
        """
        # fetch source code and save to tmp file
        result = await self.downloader.download(contract_address=contract_address)
        if isinstance(result, str) and len(result) < 5:
            file_path = os.path.join(CACHE_DIR, f"source/{contract_address}.json")
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass
                await asyncio.sleep(1)
                result = await self.downloader.download(contract_address=contract_address)
        try:
            source_code_raw = result['SourceCode']
            if source_code_raw.startswith('{') and source_code_raw.endswith('}'):
                if source_code_raw.startswith('{{'):
                    standard_json = json.loads(source_code_raw[1:-1])
                else:
                    standard_json = json.loads(source_code_raw)

                sources = standard_json.get('sources', {})
                return {fn: val['content'] for fn, val in sources.items() if 'content' in val}
            else:
                return {'': source_code_raw.replace('\r\n', '\n')}
        except Exception:
            return {'': result.get('SourceCode', '').replace('\r\n', '\n')}

    async def get_compile_item_by_json(self, contract_address: str, result: Dict) -> ContractCompileItem:
        """
        Compile the contract source code, which is fetched from etherscan.

        :param result:
        :param contract_address: the address of specific contract
        :return: the compiled result
        """
        # fetch source code and save to tmp file
        if len(result) == 0 or result.get('SourceCode') in [None, '']:
            return ContractCompileItem(contract_address, '', dict(), list(),
                                       errors=[{"severity": "error", "message": "Empty Source Code"}])

        # use solc-bin to compile standard-json,
        # and use solc-js to compile one sol source code file
        product = None
        collected_errors = []

        try:
            raw_code = result['SourceCode']
            if raw_code.startswith('{'):
                if raw_code.startswith('{{'):
                    json.loads(raw_code[1:-1])
                else:
                    json.loads(raw_code)

                product = await self._get_compile_item_by_solc(
                    contract_address=contract_address,
                    result=result,
                )
        except Exception as e:
            collected_errors.append({"severity": "warning", "message": f"Solc-bin pre-check failed: {str(e)}"})

        if product is not None and product.is_success():
            return product

        if product is not None and product.errors:
            collected_errors.extend(product.errors)

        product_js = await self._get_compile_item_by_solcjs(
            contract_address=contract_address,
            result=result,
        )

        if product_js is not None and product_js.is_success():
            return product_js

        if product_js is not None and product_js.errors:
            collected_errors.extend(product_js.errors)

        return ContractCompileItem(contract_address, '', dict(), list(), errors=collected_errors)

    async def get_compile_item_from_entity(self, entity: ContractEntityForCompile) -> ContractCompileItem:
        sources_json = {path: {"content": code} for path, code in entity.sources.items()}

        settings = copy.deepcopy(entity.compiler_settings) if entity.compiler_settings else {}

        if not settings:
            settings = {
                "optimizer": {
                    "enabled": entity.optimizations_used,
                    "runs": entity.optimization_runs if entity.optimization_runs else 200
                }
            }
            if entity.evm_version and entity.evm_version.lower() != "default":
                settings["evmVersion"] = entity.evm_version

        settings["outputSelection"] = {
            "*": {
                "*": [
                    "evm.bytecode",
                    "evm.deployedBytecode",
                    "abi"
                ],
                "": ["ast"]
            }
        }

        if entity.libraries:
            if "libraries" not in settings:
                settings["libraries"] = {}

            if isinstance(entity.libraries, dict):
                for file_name, libs in entity.libraries.items():
                    if file_name not in settings["libraries"]:
                        settings["libraries"][file_name] = {}
                    if isinstance(libs, dict):
                        settings["libraries"][file_name].update(libs)
                    else:
                        pass

        standard_json = {
            "language": "Solidity",
            "sources": sources_json,
            "settings": settings
        }

        mock_result = {
            "CompilerVersion": entity.compiler_version,
            "ContractName": entity.contract_name,
            "SourceCode": json.dumps(standard_json),
            "OptimizationUsed": "1" if entity.optimizations_used else "0",
            "Runs": str(entity.optimization_runs),
            "Library": ""
        }
        collected_errors = []

        product = await self._get_compile_item_by_solc(entity.address, mock_result)
        if product and product.is_success():
            return product
        if product and product.errors:
            collected_errors.extend(product.errors)

        product = await self._get_compile_item_by_solcjs(entity.address, mock_result)
        if product and product.is_success():
            return product
        if product and product.errors:
            collected_errors.extend(product.errors)

        return ContractCompileItem(entity.address, '', {}, [], abi=[], contract_name="", errors=collected_errors)

    async def get_compile_item(self, contract_address: str) -> ContractCompileItem:
        """
        Compile the contract source code, which is fetched from etherscan.

        :param contract_address: the address of specific contract
        :return: the compiled result
        """
        result = await self.downloader.download(contract_address=contract_address)
        if isinstance(result, str) and len(result) < 5:
            return ContractCompileItem(contract_address, '', dict(), list(),
                                       errors=[{"severity": "error", "message": "Downloader returned invalid data"}])

        if result.get('SourceCode') in [None, '']:
            return ContractCompileItem(contract_address, '', dict(), list(),
                                       errors=[{"severity": "error", "message": "SourceCode field empty"}])

        return await self.get_compile_item_by_json(contract_address, result)

    def _parse_source_json(self, result: Dict) -> Union[Dict, None]:
        raw_code = result.get('SourceCode', '')
        clean_code = self._clean_source_code(raw_code)

        if not clean_code:
            return None

        parsed_json = None

        try:
            parsed_json = json.loads(clean_code)
        except json.JSONDecodeError:
            pass

        if parsed_json is None and clean_code.startswith('{') and clean_code.endswith('}'):
            try:
                parsed_json = json.loads(clean_code[1:-1])
            except json.JSONDecodeError:
                pass

        if parsed_json is None:
            return None

        if "language" in parsed_json:
            if "settings" not in parsed_json:
                parsed_json["settings"] = {}
            if "outputSelection" not in parsed_json["settings"]:
                parsed_json["settings"]["outputSelection"] = {}

            parsed_json["settings"]["outputSelection"]["*"] = {
                "*": ["evm.bytecode", "evm.deployedBytecode", "abi"],
                "": ["ast"]
            }
            return parsed_json

        sources = {}
        for fname, content_node in parsed_json.items():
            if isinstance(content_node, dict) and "content" in content_node:
                sources[fname] = content_node
            else:
                sources[fname] = {"content": content_node}

        if not sources:
            return None

        return {
            "language": "Solidity",
            "sources": sources,
            "settings": {
                "optimizer": {
                    "enabled": result.get("OptimizationUsed", "0") == "1",
                    "runs": int(result.get("Runs", 200))
                },
                "outputSelection": {
                    "*": {
                        "*": ["evm.bytecode", "evm.deployedBytecode", "abi"],
                        "": ["ast"]
                    }
                }
            }
        }

    def _resolve_solc_path(self, compiler_version: str) -> str:
        compiler_dir = os.path.join(PROJECT_PATH, "compiler")
        if not os.path.exists(compiler_dir):
            return ""

        system_name = platform.system().lower()
        if system_name == 'windows':
            prefix = "solc-windows-amd64-"
            suffix = ".exe"
        elif system_name == 'linux':
            prefix = "solc-linux-amd64-"
            suffix = ""
        else:
            prefix = "solc-macosx-amd64-" if system_name == 'darwin' else "solc-linux-amd64-"
            suffix = ""

        version_match = re.search(r'v?(\d+\.\d+\.\d+)', compiler_version)
        if not version_match:
            target_base = compiler_version
        else:
            target_base = version_match.group(1)

        candidates = []
        try:
            files = os.listdir(compiler_dir)
        except OSError:
            return ""

        input_has_commit = "commit" in compiler_version

        for fname in files:
            if not fname.startswith(prefix):
                continue
            if suffix and not fname.endswith(suffix):
                continue

            check_ver = f"v{target_base}"
            if check_ver in fname:
                candidates.append(fname)

        if not candidates:
            return ""

        best_match = None

        for c in candidates:
            if compiler_version in c:
                best_match = c
                break

        if best_match is None and input_has_commit:
            commit_match = re.search(r'commit\.([0-9a-fA-F]+)', compiler_version)
            if commit_match:
                commit_hash = commit_match.group(1)
                for c in candidates:
                    if commit_hash in c:
                        best_match = c
                        break

        if best_match is None:
            best_match = candidates[0]

        return os.path.join(compiler_dir, best_match)

    async def _get_compile_item_by_solc(
            self, contract_address: str, result: Dict
    ) -> Union[ContractCompileItem, None]:
        """
        Compile the contract source code by `solc-bin`,
        and return the compiled item if available,
        otherwise return None.

        :param contract_address: the address of specific contract
        :param result: the result of the source code request
        :return: the compiled item or None
        """
        raw_version = result["CompilerVersion"]

        solc_path = self._resolve_solc_path(raw_version)

        if not solc_path or not os.path.exists(solc_path):
            if platform.system().lower() == 'windows':
                expected_name = f"solc-windows-amd64-{raw_version}.exe"
            else:
                expected_name = f"solc-linux-amd64-{raw_version}"
            return ContractCompileItem(contract_address, '', {}, [],
                                       errors=[
                                           {"severity": "warning",
                                            "message": f"Compiler binary not found for version {raw_version}. Expected something like {expected_name}"}])

        contract_name = result["ContractName"]
        standard_json = self._parse_source_json(result)

        if standard_json:
            product = await wrap_run4tmpfile(
                data=json.dumps(standard_json),
                async_func=lambda p: Solc(solc_path).compile_json(p, contract_name),
            )
        else:
            libraries = None
            if result.get('Library') and result['Library'] != '':
                libraries = result['Library'].split(',')
                libraries = ['{}:0x{}'.format(*lib.split(':')) for lib in libraries]

            product = await wrap_run4tmpfile(
                data=self._clean_source_code(result['SourceCode']).replace('\r\n', '\n'),
                async_func=lambda p: Solc(solc_path).compile_sol(
                    source_path=p,
                    contract_name=contract_name,
                    optimized=result["OptimizationUsed"] == '1',
                    optimize_runs=result["Runs"],
                    libraries=libraries,
                ),
            )

        # return the compilation result
        return ContractCompileItem(
            contract_address=contract_address,
            bytecode=product.bytecode,
            creation_bytecode=product.creation_bytecode,
            ast=product.ast,
            source_mapping=product.source_mapping,
            abi=product.abi,
            contract_name=contract_name,
            errors=getattr(product, 'errors', []),
            immutable_references=getattr(product, 'immutable_references', {})
        ) if product is not None else None

    async def _get_compile_item_by_solcjs(
            self, contract_address: str, result: Dict
    ) -> Union[ContractCompileItem, None]:
        """
        Compile the contract source code by `solc-js`,
        and return the compiled item if available,
        otherwise return None.

        :param contract_address: the address of specific contract
        :param result: the result of the source code request
        :return: the compiled item or None
        """
        version_match = re.search(r'v?(\d+\.\d+\.\d+)', result["CompilerVersion"])
        if version_match is None:
            return ContractCompileItem(contract_address, '', {}, [],
                                       errors=[{"severity": "error",
                                                "message": "Cannot parse compiler version for SolcJS"}])
        solc_version = 'v%s' % version_match.group(1)
        contract_name = result["ContractName"]
        _tmp_filename = "this_is_a_tmp_filename.sol"

        raw_source = result.get('SourceCode', '')
        clean_source = self._clean_source_code(raw_source)

        standard_json = self._parse_source_json(result)
        if standard_json is None:
            standard_json = {
                "language": "Solidity",
                "settings": {
                    "optimizer": {
                        "enabled": result["OptimizationUsed"] == '1',
                        "runs": int(result["Runs"]),
                    },
                },
                "sources": {
                    _tmp_filename: {
                        "content": clean_source.replace('\r\n', '\n'),
                    }
                }
            }
            if result.get('Library') and result['Library'] != '':
                libraries = result['Library'].split(',')
                standard_json['settings']['libraries'] = {
                    lib.split(':')[0]: '0x%s' % lib.split(':')[1]
                    for lib in libraries
                }
        else:
            if "sources" in standard_json:
                for f_name, _data in standard_json["sources"].items():
                    if "content" in _data:
                        _data["content"] = self._clean_source_code(_data["content"])

        if 'settings' not in standard_json:
            standard_json['settings'] = {}
        standard_json['settings']['outputSelection'] = {
            "*": {
                "*": ["evm.bytecode", "evm.deployedBytecode", "abi"],
                "": ["ast"],
            },
        }

        product = await wrap_run4tmpfile(
            data=SOLCJS_CODE % (solc_version, json.dumps(standard_json)),
            async_func=lambda p: SolcJS(NODE_PATH).compile_json(p, contract_name)
        )
        if product is None:
            return ContractCompileItem(contract_address, '', {}, [],
                                       errors=[{"severity": "error", "message": "SolcJS wrapper returned None"}])

        new_ast = {}
        for k, v in product.ast.items():
            key_name = '' if k == _tmp_filename else k
            new_ast[key_name] = v

        for item in product.source_mapping:
            if item.filename == _tmp_filename:
                item.filename = ''

        return ContractCompileItem(
            contract_address=contract_address,
            bytecode=product.bytecode,
            creation_bytecode=product.creation_bytecode,
            ast=new_ast,
            source_mapping=product.source_mapping,
            abi=product.abi,
            contract_name=contract_name,
            errors=getattr(product, 'errors', []),
            immutable_references=getattr(product, 'immutable_references', {})
        ) if product is not None else None

    async def is_contract(self, contract_address: str) -> bool:
        result = await self.downloader.download(contract_address=contract_address)
        return result != '0x'

    def _clean_source_code(self, source_code: str) -> str:
        if not source_code:
            return source_code
        if source_code.startswith('\ufeff'):
            source_code = source_code[1:]
        source_code = re.sub(r'[\xa0\u3000\u200b\t]', ' ', source_code)
        return source_code


async def test():
    pass


if __name__ == '__main__':
    asyncio.run(test())