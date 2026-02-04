from typing import Dict


class ContractEntity:
    def __init__(self, data_dict):
        self.id = data_dict.get("id")
        self.contract_id = data_dict.get("contract_id")
        self.address = data_dict.get("address")
        self.contract_name = data_dict.get("contract_name")
        self.public = data_dict.get("public")
        self.verification_date = data_dict.get("verification_date")

        self.standard = data_dict.get("standard")
        self.standards = data_dict.get("standards", [])
        self.token_data = data_dict.get("token_data")

        self.libraries = data_dict.get("libraries") or {}
        self.compiler_version = data_dict.get("compiler_version")
        self.evm_version = data_dict.get("evm_version")
        self.optimizations_used = data_dict.get("optimizations_used")
        self.optimization_runs = data_dict.get("optimization_runs")
        self.compiler_settings = data_dict.get("compiler_settings")

        self.data = data_dict.get("data") or {}
        self.abi = self.data.get("abi") or []
        self.contract_info = self.data.get("contract_info") or []
        self.states = self.data.get("states") or []
        self.methods = self.data.get("methods") or []

        self.deployed_bytecode = data_dict.get("deployed_bytecode")
        self.creation_bytecode = data_dict.get("creation_bytecode")

        self.src_map = data_dict.get("src_map")

        self.file_map: Dict[int, str] = {
            f.get("id"): f.get("source", "") for f in self.contract_info
        }

    @property
    def has_source_code(self) -> bool:
        return len(self.contract_info) > 0 and any(c.get("source") for c in self.contract_info)

    @property
    def has_abi(self) -> bool:
        return len(self.abi) > 0

    def get_contract_status(self):
        if self.has_source_code:
            status_code = "FULLY_OPEN_SOURCE"
            description = "The contract has been verified and made open source, providing complete source code and ABI."
        elif self.has_abi and not self.has_source_code:
            status_code = "ABI_ONLY"
            description = "The contract is not open source or only provides the ABI. The source code is not visible; only the interface definitions are available."
        elif self.deployed_bytecode and self.deployed_bytecode != "0x":
            status_code = "BYTECODE_ONLY"
            description = "Black-box contract. There is no source code, no ABI, only the deployed bytecode."
        else:
            status_code = "EMPTY_OR_UNREACHABLE"
            description = "Unable to obtain contract information or the contract has not been deployed yet."
        return status_code, description

    @classmethod
    def from_dict(cls, data_dict):
        return cls(data_dict)

    def to_dict(self):
        return self.__dict__


class ContractEntityForCompile:
    def __init__(self, data_dict):
        self.address = data_dict.get("address")
        self.full_file_map = data_dict.get("full_file_map")
        self.sources = data_dict.get("sources")
        self.contract_name = data_dict.get("contract_name")
        self.compiler_version = data_dict.get("compiler_version")
        self.evm_version = data_dict.get("evm_version")
        self.optimizations_used = data_dict.get("optimizations_used")
        self.optimization_runs = data_dict.get("optimization_runs")
        self.compiler_settings = data_dict.get("compiler_settings")
        self.libraries = data_dict.get("libraries")

    @classmethod
    def from_dict(cls, data_dict):
        return cls(data_dict)

    def __repr__(self):
        return f"<ContractForCompile name={self.contract_name} version={self.compiler_version}>"
