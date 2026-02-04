import asyncio
import logging
import os
import shutil
import tempfile
import platform
import aiohttp
import aiofiles
from pathlib import Path
from typing import Optional, Tuple, List, Dict
from settings import PROJECT_PATH

logger = logging.getLogger("Decompiler")


class ExternalDecompiler:
    OPENCHAIN_URL = "https://api.openchain.xyz/signature-database/v1/lookup"

    async def resolve_selectors(self, selectors: List[str]) -> Dict[str, str]:
        if not selectors:
            return {}

        valid_selectors = list(set([s for s in selectors if s.startswith("0x") and len(s) == 10]))
        if not valid_selectors:
            return {}

        params = {
            "function": ",".join(valid_selectors),
            "filter": "true"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.OPENCHAIN_URL, params=params, timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        result = data.get("result", {}).get("function", {})

                        formatted_res = {}
                        for sel, matches in result.items():
                            if matches and len(matches) > 0:
                                formatted_res[sel] = matches[0].get("name", "Unknown")
                        return formatted_res
                    else:
                        logger.warning(f"OpenChain API returned status: {resp.status}")
                        return {}
        except Exception as e:
            logger.warning(f"OpenChain lookup failed (network issue?): {e}")
            return {}


class HeimdallWrapper:

    def __init__(self, custom_path: str = None):
        self.is_windows = platform.system().lower() == "windows"
        self.binary_name = "heimdall.exe" if self.is_windows else "heimdall"
        self.executable = self._find_binary(custom_path)

        if self.executable:
            logger.info(f"Heimdall binary found at: {self.executable}")
        else:
            logger.warning("Heimdall binary NOT found. Decompilation will fail.")

    def _find_binary(self, custom_path: str = None) -> Optional[str]:
        if custom_path and os.path.exists(custom_path):
            return custom_path

        env_path = os.environ.get("HEIMDALL_BIN_PATH")
        if env_path:
            if self.is_windows and not env_path.lower().endswith(".exe"):
                if os.path.exists(env_path + ".exe"): return env_path + ".exe"
            if os.path.exists(env_path):
                return env_path

        project_bin = os.path.join(PROJECT_PATH, "bin", self.binary_name)
        if os.path.exists(project_bin):
            return project_bin

        home_dir = Path.home()
        default_install = home_dir / ".bifrost" / "bin" / self.binary_name
        if default_install.exists():
            return str(default_install)

        which_path = shutil.which("heimdall")
        if which_path:
            return which_path

        return None

    def is_available(self) -> bool:
        return self.executable is not None

    async def decompile(self, bytecode: str, timeout: int = 30) -> Tuple[bool, str]:
        if not self.is_available():
            return False, "Heimdall binary is not configured or found on this system."

        if bytecode.startswith("0x"):
            bytecode = bytecode[2:]

        if len(bytecode) == 0:
            return False, "Empty bytecode provided."

        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                cmd = [
                    self.executable, "decompile", bytecode,
                    "--output", temp_dir,
                    "--default",
                    "--include-sol"
                ]

                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

                try:
                    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
                except asyncio.TimeoutError:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    return False, f"Decompilation timed out after {timeout} seconds."

                found_sol_file = None
                for file_name in os.listdir(temp_dir):
                    if file_name.endswith(".sol"):
                        found_sol_file = os.path.join(temp_dir, file_name)
                        break

                if found_sol_file:
                    async with aiofiles.open(found_sol_file, mode='r', encoding='utf-8') as f:
                        content = await f.read()

                    cleaned_code = self._clean_output(content)
                    if not cleaned_code.strip():
                        return False, "Heimdall produced empty output."

                    return True, cleaned_code
                else:
                    err_msg = stderr.decode(errors='replace').strip()
                    clean_err = '\n'.join([line for line in err_msg.splitlines()
                                           if "error" in line.lower() or "critical" in line.lower()])
                    if not clean_err:
                        clean_err = "No .sol file generated. Bytecode might be invalid or abstract."
                    return False, f"Decompilation failed: {clean_err}"

            except Exception as e:
                logger.error(f"Unexpected error in HeimdallWrapper: {e}")
                return False, f"Internal wrapper error: {str(e)}"

    def _clean_output(self, content: str) -> str:
        lines = content.splitlines()
        cleaned = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("///") and "SPDX" not in line:
                continue
            if "Decompiled with Heimdall" in line:
                continue
            cleaned.append(line)
        return "\n".join(cleaned)
