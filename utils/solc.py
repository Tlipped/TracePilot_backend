import asyncio
import json
from asyncio import subprocess
from typing import Dict, List, Union, Any


class SourceMappingItem:
    def __init__(
            self, begin: int, offset: int,
            filename: str, opcode: str, pc: int = -1,
    ):
        self.begin = begin
        self.offset = offset
        self.filename = filename
        self.opcode = opcode
        self.pc = pc

    def to_dict(self) -> Dict[str, Any]:
        return {
            "begin": self.begin,
            "offset": self.offset,
            "filename": self.filename,
            "opcode": self.opcode,
            "pc": self.pc
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SourceMappingItem':
        return cls(
            begin=data.get("begin", -1),
            offset=data.get("offset", -1),
            filename=data.get("filename", ""),
            opcode=data.get("opcode", ""),
            pc=data.get("pc", -1)
        )

    def __str__(self):
        return "{}:{} {} {} {}".format(
            self.begin,
            self.offset,
            self.filename,
            self.opcode,
            self.pc,
        )


class CompileResult:
    def __init__(self, bytecode: str, creation_bytecode: str, ast: Dict[str, Dict],
                 source_mapping: List[SourceMappingItem], abi: List = None, errors: List[Dict] = None,
                 immutable_references: Dict = None):
        self.bytecode = bytecode
        self.creation_bytecode = creation_bytecode
        self.ast = ast
        self.source_mapping = source_mapping
        self.abi = abi if abi else []
        self.errors = errors if errors else []
        self.immutable_references = immutable_references if immutable_references else {}


class Solc:
    """
    A solidity code compiler, based on `solc-bin`.
    """

    def __init__(self, path: str, timeout: float = 60.0):
        self.path = path
        self.timeout = timeout

    async def compile_json(
            self, standard_json_path: str, contract_name: str
    ) -> Union[CompileResult, None]:
        """
        Compile the source code by standard json.

        :param standard_json_path: The standard json path.
        :param contract_name: Name of target contract
        :return: a compacted result json.
        """
        cmd = [self.path, '--standard-json', standard_json_path]
        try:
            process = await subprocess.create_subprocess_shell(
                ' '.join(cmd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            output, err_out = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            return CompileResult("", "", {}, [], [], errors=[{"severity": "error", "message": "Compilation Timed Out"}])
        except Exception as e:
            return CompileResult("", "", {}, [], [],
                                 errors=[{"severity": "error", "message": f"Subprocess Error: {str(e)}"}])

        try:
            output_json = json.loads(output.decode())
        except json.JSONDecodeError:
            err_msg = err_out.decode().strip() if err_out else "Empty Output"
            return CompileResult("", "", {}, [], [],
                                 errors=[
                                     {"severity": "error", "message": f"JSON Decode Error or Solc Crash: {err_msg}"}])

        compilation_errors = output_json.get("errors", [])

        if "contracts" not in output_json or not output_json["contracts"]:
            if not compilation_errors and err_out:
                compilation_errors.append({"severity": "error", "message": err_out.decode()})
            return CompileResult("", "", {}, [], abi=[], errors=compilation_errors)

        contracts = output_json["contracts"]
        sources = output_json["sources"]
        target_contract = None

        found = False
        for path in contracts.keys():
            if contract_name in contracts[path]:
                target_contract = contracts[path]
                found = True
                break

        if not found:
            for path in contracts.keys():
                for _c_name in contracts[path].keys():
                    if _c_name == contract_name:
                        target_contract = contracts[path]
                        found = True
                        break
                if found: break

        if not target_contract:
            compilation_errors.append(
                {"severity": "error", "message": f"Contract {contract_name} not found in compilation output"})
            return CompileResult("", "", {}, [], [], errors=compilation_errors)

        try:
            # extract the source info, e.g., ast, source id
            ast_dict = {k: v.get('ast', {}) for k, v in sources.items()}
            idx2path = {v['id']: k for k, v in sources.items() if 'id' in v}

            contract_data = target_contract[contract_name]
            evm_data = contract_data.get("evm", {})

            deployed_bytecode_obj = evm_data.get("deployedBytecode", {})
            bytecode = deployed_bytecode_obj.get("object", "")

            immutable_references = deployed_bytecode_obj.get("immutableReferences", {})

            # Creation Bytecode
            bytecode_obj = evm_data.get("bytecode", {})
            creation_bytecode = bytecode_obj.get("object", "")

            abi = contract_data.get("abi", [])

            opcodes = deployed_bytecode_obj.get("opcodes", "")
            source_map = deployed_bytecode_obj.get("sourceMap", "")

            source_mapping_items = self._get_source_mappings(
                opcodes_str=opcodes,
                source_map=source_map,
                idx2path=idx2path,
            )

            return CompileResult(
                bytecode=bytecode,
                creation_bytecode=creation_bytecode,
                ast=ast_dict,
                source_mapping=source_mapping_items,
                abi=abi,
                errors=compilation_errors,
                immutable_references=immutable_references  # [NEW]
            )
        except Exception as e:
            compilation_errors.append({"severity": "error", "message": f"Result Parsing Error: {str(e)}"})
            return CompileResult("", "", {}, [], abi=[], errors=compilation_errors)

    async def compile_sol(
            self, source_path: str,
            contract_name: str, optimized: bool,
            optimize_runs: str = '200',
            libraries: Union[List[str], None] = None,
    ) -> Union[CompileResult, None]:
        """
        Compile the source code using the source file directly.
        Added 'bin-runtime' to fetch deployed bytecode specifically.
        :param source_path: The source file path.
        :param contract_name: Name of target contract
        :param optimized: whether do an optimization or not
        :param optimize_runs: optimization runs
        :return: a compacted result json.

        """
        cmd = [self.path, '--combined-json', 'bin,bin-runtime,ast,opcodes,srcmap,compact-format,abi']
        if optimized is True:
            cmd.extend(['--optimize', '--optimize-runs', str(optimize_runs)])
        if libraries is not None:
            libraries = ','.join(['%s:%s' % (source_path, lib) for lib in libraries])
            cmd.append('--libraries %s' % libraries)
        cmd.append(source_path)

        try:
            process = await subprocess.create_subprocess_shell(
                ' '.join(cmd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            output, err_out = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout
            )
        except asyncio.TimeoutError:
            return CompileResult("", "", {}, [], [], errors=[{"severity": "error", "message": "Compilation Timed Out"}])
        except Exception as e:
            return CompileResult("", "", {}, [], [],
                                 errors=[{"severity": "error", "message": f"Subprocess Error: {str(e)}"}])

        err_msg = err_out.decode().strip()
        if err_msg and "Error:" in err_msg:
            if "Error:" in err_msg:
                return CompileResult("", "", {}, [], [], errors=[{"severity": "error", "message": err_msg}])

        try:
            output_json = json.loads(output.decode())
        except Exception:
            return CompileResult("", "", {}, [], [],
                                 errors=[{"severity": "error", "message": f"Invalid JSON output: {err_msg}"}])

        try:
            contracts = output_json.get("contracts", {})
            sources = output_json.get("sources", {})
            target = source_path + ':' + contract_name

            target_data = contracts.get(target)
            if not target_data:
                for k, v in contracts.items():
                    if k.endswith(':' + contract_name):
                        target_data = v
                        break

            if not target_data:
                return CompileResult("", "", {}, [], [],
                                     errors=[{"severity": "error",
                                              "message": f"Contract {target} not found in {contracts.keys()}"}])

            bytecode = target_data.get('bin-runtime', '')
            creation_bytecode = target_data.get('bin', '')

            opcodes = target_data.get('opcodes', '')
            srcmap = target_data.get('srcmap', '')

            idx2path = {0: source_path}

            source_mapping_items = self._get_source_mappings(
                opcodes_str=opcodes,
                source_map=srcmap,
                idx2path=idx2path,
            )
            for item in source_mapping_items:
                if item.filename == source_path:
                    item.filename = ''

            abi = target_data.get('abi', [])
            if isinstance(abi, str):
                abi = json.loads(abi)

            ast_data = {}
            if source_path in sources:
                ast_data = {'': sources[source_path].get('AST', {})}

            return CompileResult(
                bytecode=bytecode,
                creation_bytecode=creation_bytecode,
                ast=ast_data,
                source_mapping=source_mapping_items,
                abi=abi,
                immutable_references={}
            )
        except Exception as e:
            return CompileResult("", "", {}, [], abi=[],
                                 errors=[{"severity": "error", "message": f"Parsing Error: {str(e)}"}])

    def _get_source_mappings(
            self, opcodes_str: str, source_map: str,
            idx2path: Dict[int, str],
    ) -> List[SourceMappingItem]:
        if not opcodes_str:
            return []
        # decompile the bytecode
        push2size = {'PUSH%d' % i: i for i in range(1, 32 + 1)}
        source_mapping_items, pc = list(), 0
        opcodes = opcodes_str.split(' ')
        for op in opcodes:
            if op.startswith('0x'):
                continue
            source_mapping_items.append(SourceMappingItem(
                begin=-1, offset=-1, filename='',
                opcode=op, pc=pc,
            ))
            pc += 1 if not op.startswith('PUSH') else 1 + push2size[op]

        if not source_map:
            return source_mapping_items

        # map the pc to source
        prev_s, prev_l, prev_f, prev_j = None, None, None, None
        source_map_parts = source_map.split(';')
        for i, _source_map in enumerate(source_map_parts):
            vals = [prev_s, prev_l, prev_f, prev_j]
            parts = _source_map.split(':')
            for j, val in enumerate(parts):
                if val == '' or j >= len(vals):
                    continue
                if val == '-1':
                    vals[j] = -1
                elif val.isdigit():
                    vals[j] = int(val)

            prev_s, prev_l, prev_f, prev_j = vals

            if i < len(source_mapping_items):
                if vals[0] is not None: source_mapping_items[i].begin = vals[0]
                if vals[1] is not None: source_mapping_items[i].offset = vals[1]

                if vals[2] is not None and vals[2] != -1:
                    if vals[2] in idx2path:
                        source_mapping_items[i].filename = idx2path[vals[2]]

        # return the result
        source_mapping_items = source_mapping_items[: min(len(opcodes), len(source_map_parts))]
        source_mapping_items = [item for item in source_mapping_items if item.filename != '']
        return source_mapping_items


class SolcJS(Solc):
    """
    A solidity code compiler, based on `solc-js`.
    """

    async def compile_json(
            self, standard_json_path: str, contract_name: str
    ) -> Union[CompileResult, None]:
        cmd = [self.path, '--stack-size=65536', standard_json_path]

        try:
            process = await subprocess.create_subprocess_shell(
                ' '.join(cmd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            output, err_out = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout,
            )

        except asyncio.TimeoutError:
            return CompileResult("", "", {}, [], [], errors=[{"severity": "error", "message": "SolcJS Timed Out"}])
        except Exception as e:
            return CompileResult("", "", {}, [], [],
                                 errors=[{"severity": "error", "message": f"SolcJS Process Error: {str(e)}"}])

        try:
            product = json.loads(output.decode())
        except Exception:
            raw_out = output.decode()
            try:
                start_idx = raw_out.find('{')
                end_idx = raw_out.rfind('}')
                if start_idx != -1 and end_idx != -1:
                    product = json.loads(raw_out[start_idx:end_idx + 1])
                else:
                    return CompileResult("", "", {}, [], [], errors=[
                        {"severity": "error", "message": f"SolcJS Execution Error: {err_out.decode()}"}])
            except:
                return CompileResult("", "", {}, [], [], errors=[
                    {"severity": "error", "message": f"SolcJS Output Invalid JSON: {raw_out[:100]}..."}])

        compilation_errors = product.get("errors", [])

        cleaned_errors = []
        for e in compilation_errors:
            if isinstance(e, str):
                cleaned_errors.append({"severity": "error", "message": e})
            elif isinstance(e, dict):
                cleaned_errors.append(e)
            else:
                cleaned_errors.append({"severity": "error", "message": str(e)})
        compilation_errors = cleaned_errors

        # parse data
        contracts = product.get("contracts", dict())
        sources = product.get("sources", dict())

        if not contracts:
            has_error = any((isinstance(e, dict) and e.get('severity') == 'error') or not isinstance(e, dict) for e in compilation_errors)
            if has_error:
                return CompileResult("", "", {}, [], [], errors=compilation_errors)

        target_contract = None
        for path in contracts.keys():
            if contract_name in contracts[path]:
                target_contract = contracts[path]
                break

        if not target_contract:
            for path in contracts.keys():
                for c_name in contracts[path].keys():
                    if c_name == contract_name:
                        target_contract = contracts[path]
                        break
                if target_contract: break

        if not target_contract:
            return CompileResult("", "", {}, [], [], errors=compilation_errors + [
                {"severity": "error", "message": f"Contract {contract_name} not found"}])

        try:
            ast_dict = {k: v.get('ast', {}) for k, v in sources.items()}
            idx2path = {v['id']: k for k, v in sources.items() if 'id' in v}

            evm_data = target_contract[contract_name]["evm"]
            bytecode = evm_data["deployedBytecode"]["object"]

            immutable_references = evm_data["deployedBytecode"].get("immutableReferences", {})

            # Creation Bytecode
            creation_bytecode = ""
            if "bytecode" in evm_data and "object" in evm_data["bytecode"]:
                creation_bytecode = evm_data["bytecode"]["object"]

            abi = target_contract[contract_name].get("abi", [])

            source_mapping_items = self._get_source_mappings(
                opcodes_str=evm_data["deployedBytecode"]["opcodes"],
                source_map=evm_data["deployedBytecode"]["sourceMap"],
                idx2path=idx2path,
            )
            return CompileResult(bytecode, creation_bytecode, ast_dict, source_mapping_items, abi=abi,
                                 errors=compilation_errors,
                                 immutable_references=immutable_references)
        except Exception as e:
            return CompileResult("", "", {}, [], abi=[], errors=compilation_errors + [
                {"severity": "error", "message": f"SolcJS Parsing Error: {str(e)}"}])