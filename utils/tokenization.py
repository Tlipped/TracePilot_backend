import logging
from typing import List, Union

try:
    import tiktoken
except ImportError:
    tiktoken = None

logger = logging.getLogger(__name__)


TokenUnit = Union[int, str]


class ApproxEncoding:
    """Offline fallback used when tiktoken cannot fetch its BPE assets."""

    def __init__(self, chars_per_token: int = 4):
        self.chars_per_token = chars_per_token

    def encode(self, text: str) -> List[TokenUnit]:
        value = str(text)
        return [value[i:i + self.chars_per_token] for i in range(0, len(value), self.chars_per_token)]

    def decode(self, tokens: List[TokenUnit]) -> str:
        return "".join(str(token) for token in tokens)


def get_token_encoder(model: str | None = None):
    if tiktoken is None:
        logger.warning("Falling back to ApproxEncoding because tiktoken is not installed.")
        return ApproxEncoding()

    try:
        if model:
            return tiktoken.encoding_for_model(model)
        return tiktoken.get_encoding("cl100k_base")
    except Exception as first_error:
        try:
            return tiktoken.get_encoding("cl100k_base")
        except Exception as second_error:
            logger.warning(
                "Falling back to ApproxEncoding because tiktoken assets are unavailable: %s; %s",
                first_error,
                second_error,
            )
            return ApproxEncoding()
