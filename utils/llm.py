import json
import os
import re
import time

from openai import OpenAI
from string import Template

from settings import LLM_BASE_URL, LLM_API_KEY, LLM_NAME, PROJECT_PATH, PROMPT_PATH
from utils.tokenization import get_token_encoder

enc = get_token_encoder("gpt-4o")

MODEL_CONTEXT_WINDOWS = {
    # --- OpenAI ---
    "gpt-4o": 128000,
    "gpt-4o-2024-08-06": 128000,
    "gpt-4o-mini": 128000,
    "o1-preview": 128000,
    "o1-mini": 128000,

    # --- Google Gemini ---
    "gemini-1.5-pro": 2000000,
    "gemini-1.5-flash": 1000000,
    "gemini-2.0-flash": 1000000,
    "gemini-3.0-pro": 2000000,

    # --- Xiaomi MiMo ---
    "mimo-v2.5-pro": 1048576,

    # --- Anthropic Claude ---
    "claude-3-5-sonnet-20241022": 200000,
    "claude-3-5-haiku": 200000,
    "claude-3-opus": 200000,

    # --- DeepSeek ---
    "deepseek-chat": 64000,
    "deepseek-reasoner": 64000,

    # --- Open Source / Llama ---
    "llama-3.1-405b-instruct": 128000,
    "llama-3.1-70b-instruct": 128000,

    # --- Fallback ---
    "default": 4096
}

MODEL_MAX_OUTPUT_TOKENS = {
    # --- OpenAI ---
    "gpt-4o": 14000,
    "gpt-4o-2024-08-06": 16000,
    "gpt-4o-mini": 16000,
    "o1-preview": 32768,
    "o1-mini": 65536,

    # --- Google Gemini ---
    "gemini-1.5-pro": 8192,
    "gemini-1.5-flash": 8192,
    "gemini-2.0-flash": 8192,
    "gemini-3.0-pro": 8192,

    # --- Xiaomi MiMo ---
    "mimo-v2.5-pro": 131072,

    # --- Anthropic Claude ---
    "claude-3-5-sonnet-20241022": 8192,
    "claude-3-5-haiku": 8192,
    "claude-3-opus": 4096,

    # --- DeepSeek ---
    "deepseek-chat": 8192,
    "deepseek-reasoner": 32768,

    # --- Open Source / Llama ---
    "llama-3.1-405b-instruct": 8192,
    "llama-3.1-70b-instruct": 8192,

    # --- Fallback ---
    "default": 8192
}


def load_prompt(file_name: str, variables: dict = None) -> str:
    file_path = os.path.join(PROJECT_PATH, PROMPT_PATH, file_name)
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"The path does not exist.: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        template = Template(f.read())
        return template.substitute(variables or {})


def call_llm(system_prompt: str, user_prompt: str) -> (str, int):
    client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
    try:
        start_time = time.time()
        response = client.chat.completions.create(
            model=LLM_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            stream=False
        )
        end_time = time.time()
        elapsed_time = end_time - start_time
        token = int(response.usage.total_tokens)
        result = response.choices[0].message.content.strip()
        finish_reason = response.choices[0].finish_reason.strip()
    except Exception as e:
        error_msg = f"Error occurred during LLM analysis: {str(e)}"
        print(error_msg)
        return error_msg, 0

    if response is None:
        blank_msg = "LLM returned empty data"
        print(blank_msg)
        return blank_msg, 0
    if finish_reason == 'length':
        length_msg = "Input exceeds LLM context limit"
        print(length_msg)
        return length_msg, 0
    print(format_response_display(result, token, finish_reason, str(elapsed_time)))
    return result, token


def format_prompt_display(system_prompt: str, user_prompt: str) -> str:
    """Format the prompt display for better readability."""
    separator = "=" * 80
    display = [separator, "🤖 LLM Call Details", separator, "\n📋 System Prompt:", "-" * 40, system_prompt,
               "\n👤 User Prompt:", "-" * 40, user_prompt, "\n📊 Prompt Statistics:", "-" * 40]
    system_tokens = enc.encode(system_prompt)
    user_tokens = enc.encode(user_prompt)
    display.append(f"System prompt tokens: {len(system_tokens)} ")
    display.append(f"User prompt tokens: {len(user_tokens)} ")
    display.append(f"Total prompt tokens: {len(system_tokens) + len(user_tokens)} ")
    display.append(separator)
    return "\n".join(display)


def format_response_display(result: str, token: int, finish_reason: str, elapsed_time: str) -> str:
    """Format the LLM response display for better readability."""
    separator = "=" * 80
    display = [separator, f"✨ LLM Response Result, Duration: {elapsed_time}s", separator, "\n📝 Model Response Content:", "-" * 40, result, "\n📈 Response Metadata:",
               "-" * 40,
               f"Tokens Consumed: {token}", f"Finish Reason: {finish_reason}", f"Response Length: {len(result)} characters", separator]
    return "\n".join(display)


def parse_llm_json(text: str) -> dict:
    """
        LLM JSON Parser
        Capable of handling:
        1. Pure JSON strings
        2. JSON wrapped in Markdown code blocks (```json ... ```)
        3. JSON mixed within conversational text
    """
    if not text:
        return {}

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Attempt to extract Markdown code blocks (most common case)
    # Regex explanation: Matches starting with ``` or ```json, captures non-greedy content in between, ends with ```
    markdown_pattern = r"```(?:json)?\s*(\{.*?\})\s*```"
    match = re.search(markdown_pattern, text, re.DOTALL)
    if match:
        try:
            json_str = match.group(1)
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    # Brute force extraction of the outermost braces, looking for the first '{' and the last '}'
    try:
        start_idx = text.find('{')
        end_idx = text.rfind('}')

        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_str = text[start_idx: end_idx + 1]
            return json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"Error parsing extracted JSON segment: {e}")
        pass

    print(f"Failed to parse JSON from LLM output. Raw length: {len(text)}")
    return {}
