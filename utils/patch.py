import re
from typing import Dict, Tuple, List, Optional


class SolidityCodePatcher:
    def __init__(self, sources_map: Dict[str, str]):
        self.sources = {k: v.splitlines() for k, v in sources_map.items()}
        self.modified_sources = self.sources.copy()

    def apply_patches(self, llm_response: str) -> Tuple[bool, str, Dict[str, str]]:
        llm_response = llm_response.replace('\r\n', '\n')

        file_blocks = self._split_by_file(llm_response)
        if not file_blocks:
            return False, "Error: No file blocks found (missing '# File: ...').", {}

        logs = []
        overall_success = True

        for file_id, blocks in file_blocks.items():
            if file_id not in self.modified_sources:
                logs.append(f"Warning: File '{file_id}' not found in source map. Skipping.")
                overall_success = False
                continue

            for block_index, content_block in enumerate(blocks):
                success, log = self._apply_single_block(file_id, content_block)
                status = "SUCCESS" if success else "ERROR"
                logs.append(f"[{status}] File: {file_id} (Block {block_index + 1}) | {log}")

                if not success:
                    overall_success = False

        final_sources = {k: "\n".join(lines) for k, lines in self.modified_sources.items()}
        return overall_success, "\n".join(logs), final_sources

    def _split_by_file(self, text: str) -> Dict[str, List[str]]:
        pattern = re.compile(r'^#\s*File:\s*(.+?)\s*$', re.MULTILINE)
        matches = list(pattern.finditer(text))
        result: Dict[str, List[str]] = {}

        if not matches:
            return {}

        for i, match in enumerate(matches):
            file_id = match.group(1).strip()
            start_idx = match.end()
            end_idx = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            content = text[start_idx:end_idx]

            if file_id not in result:
                result[file_id] = []
            result[file_id].append(content)
        return result

    def _apply_single_block(self, file_id: str, patch_text: str) -> Tuple[bool, str]:
        current_lines = self.modified_sources[file_id]

        # 极度宽容的正则：允许 SEARCH 后有空格，允许换行符差异
        pattern = re.compile(
            r'<<<<<<< SEARCH\s*\n(.*?)\n=======\s*\n(.*?)\n>>>>>>> REPLACE',
            re.DOTALL
        )
        matches = pattern.findall(patch_text)

        if not matches:
            return True, "No valid SEARCH/REPLACE patterns found in this block."

        applied_count = 0

        for search_block, replace_block in matches:
            search_lines = search_block.splitlines()
            replace_lines = replace_block.splitlines()

            match_start, match_end, status = self._robust_find_index(current_lines, search_lines)

            if status == -1:
                trimmed_search_lines = self._trim_empty_lines(search_lines)
                if len(trimmed_search_lines) < len(search_lines):
                    m_start2, m_end2, status2 = self._robust_find_index(current_lines, trimmed_search_lines)
                    if status2 == 0:
                        search_lines = trimmed_search_lines
                        match_start, match_end, status = m_start2, m_end2, status2

            if status == -1:
                return False, (
                    "SEARCH block not found (after stripping comments & trimming).\n"
                    f"Context:\n{search_block}"
                )
            if status == -2:
                return False, (
                    "SEARCH block is ambiguous (multiple occurrences found after stripping comments)."
                )

            target_indent = self._get_indent_string(current_lines[match_start])
            final_replace_lines = self._reindent_block(replace_lines, target_indent)

            end_index_exclusive = match_end + 1
            current_lines[match_start: end_index_exclusive] = final_replace_lines
            applied_count += 1

        self.modified_sources[file_id] = current_lines
        return True, f"Applied {applied_count} patches successfully."

    def _robust_find_index(
            self,
            source_lines: List[str],
            search_lines: List[str]
    ) -> Tuple[int, int, int]:
        if not search_lines:
            return -1, -1, -1

        search_code_tokens: List[str] = []
        in_block_comment = False
        for line in search_lines:
            code_part, in_block_comment = self._strip_comments_from_line(line, in_block_comment)
            normalized = self._normalize_line(code_part)
            if normalized:
                search_code_tokens.append(normalized)

        if not search_code_tokens:
            return -1, -1, -1

        code_entries: List[Tuple[int, str]] = []
        in_block_comment = False
        for idx, line in enumerate(source_lines):
            code_part, in_block_comment = self._strip_comments_from_line(line, in_block_comment)
            normalized = self._normalize_line(code_part)
            if normalized:
                code_entries.append((idx, normalized))

        if len(search_code_tokens) > len(code_entries):
            return -1, -1, -1

        found_spans: List[Tuple[int, int]] = []
        n_search = len(search_code_tokens)

        for i in range(len(code_entries) - n_search + 1):
            if code_entries[i][1] != search_code_tokens[0]:
                continue

            match = True
            for j in range(1, n_search):
                if code_entries[i + j][1] != search_code_tokens[j]:
                    match = False
                    break

            if match:
                start_original = code_entries[i][0]
                end_original = code_entries[i + n_search - 1][0]
                found_spans.append((start_original, end_original))

        if len(found_spans) == 1:
            return found_spans[0][0], found_spans[0][1], 0
        elif len(found_spans) > 1:
            return -1, -1, -2
        else:
            return -1, -1, -1

    def _reindent_block(self, lines: List[str], target_indent: str) -> List[str]:
        if not lines:
            return []

        non_empty_lines = [l for l in lines if l.strip()]
        if not non_empty_lines:
            return lines

        indents = [len(l) - len(l.lstrip()) for l in non_empty_lines]
        min_indent_len = min(indents)

        reindented_lines: List[str] = []
        for line in lines:
            if not line.strip():
                reindented_lines.append("")
                continue
            content = line[min_indent_len:]

            reindented_lines.append(target_indent + content)

        return reindented_lines

    def _normalize_line(self, line: str) -> str:
        return "".join(line.split())

    def _get_indent_string(self, line: str) -> str:
        return line[:len(line) - len(line.lstrip())]

    def _trim_empty_lines(self, lines: List[str]) -> List[str]:
        start = 0
        while start < len(lines) and not lines[start].strip():
            start += 1

        end = len(lines)
        while end > start and not lines[end - 1].strip():
            end -= 1

        return lines[start:end]

    def _strip_comments_from_line(self, line: str, in_block_comment: bool) -> Tuple[str, bool]:
        if not line:
            return "", in_block_comment

        i = 0
        n = len(line)
        result_chars: List[str] = []

        while i < n:
            if in_block_comment:
                end_pos = line.find("*/", i)
                if end_pos == -1:
                    return "".join(result_chars), True
                else:
                    in_block_comment = False
                    i = end_pos + 2
                    continue

            if i + 1 < n:
                two = line[i:i + 2]
                if two == "//":
                    break
                if two == "/*":
                    in_block_comment = True
                    i += 2
                    continue

            result_chars.append(line[i])
            i += 1

        return "".join(result_chars), in_block_comment
