import warnings
import os
from ..logger import get_logger
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from dataflow.core import LLMServingABC
from .llm_client import LLMClientAdapter


class APILLMServing_request(LLMServingABC):
    """Use OpenAI API to generate responses based on input messages."""

    def start_serving(self) -> None:
        self.logger.info("APILLMServing_request: no local service to start.")
        return

    def __init__(
        self,
        api_url: str = "http://localhost:8000/v1",
        key_name_of_api_key: str = "DF_API_KEY",
        model_name: str = "gpt-4o",
        temperature: float = 0.0,
        max_workers: int = 10,
        max_retries: int = 5,
        read_timeout: float = 120.0,
        **configs: dict,
    ):
        """Initialize the LLM serving client.

        Args:
            api_url: The API base URL (e.g., "http://localhost:8000/v1")
            key_name_of_api_key: Environment variable name for API key
            model_name: Default model name
            temperature: Sampling temperature
            max_workers: Maximum concurrent threads
            max_retries: Maximum retry attempts
            read_timeout: Read timeout in seconds
            configs: Additional config parameters
        """
        self.api_url = api_url.rstrip("/")
        self.model_name = model_name
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.read_timeout = read_timeout

        # Handle deprecated 'timeout' parameter
        if "timeout" in configs:
            warnings.warn(
                "The `timeout` parameter is deprecated. Please use `read_timeout` instead.",
                DeprecationWarning,
            )
            self.read_timeout = configs["timeout"]
            configs.pop("timeout")

        self.configs = configs
        self.configs.update({"temperature": temperature})

        self.logger = get_logger()

        # Get API key from environment
        self.api_key = os.environ.get(key_name_of_api_key)
        if self.api_key is None:
            error_msg = f"Lack of `{key_name_of_api_key}` in environment variables. Please set `{key_name_of_api_key}` as your api-key to {api_url} before using APILLMServing_request."
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        # Create the unified LLM client
        client_params = {
            "model": model_name,
            "read_timeout": read_timeout,
            "temperature": temperature,
        }
        # Merge additional configs (exclude client-only params)
        for key, value in self.configs.items():
            if key not in ("model", "read_timeout", "temperature", "max_workers"):
                client_params[key] = value

        self.client = LLMClientAdapter(
            api_url=self.api_url,
            api_key=self.api_key,
            client_params=client_params,
        )

    def generate_from_input(
        self,
        user_inputs: list[str],
        system_prompt: str = "You are a helpful assistant",
        json_schema: dict = None,
    ) -> list[str]:
        """Generate responses from a list of user inputs.

        Args:
            user_inputs: List of user input strings
            system_prompt: System prompt to prepend
            json_schema: Optional JSON schema for structured output

        Returns:
            List of responses in input order
        """
        # Build messages for each input
        messages_list = [
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ]
            for question in user_inputs
        ]

        return self._run_batch(messages_list, json_schema, "Generating responses......")

    def generate_from_conversations(
        self, conversations: list[list[dict]], json_schema: dict = None
    ) -> list[str]:
        """Generate responses from a list of conversation histories.

        Args:
            conversations: List of conversation histories (each is a list of message dicts)
            json_schema: Optional JSON schema for structured output

        Returns:
            List of responses in conversation order
        """
        return self._run_batch(
            conversations, json_schema, "Generating responses from conversations......"
        )

    def _run_batch(
        self, messages_list: list[list[dict]], json_schema: dict, desc: str
    ) -> list[str]:
        """
        Run batch sync calls using thread pool.

        Args:
            messages_list: List of message lists
            json_schema: Optional JSON schema
            desc: Progress bar description

        Returns:
            List of responses ordered by input order
        """
        responses = [None] * len(messages_list)

        def run_single(idx: int, messages: list[dict]) -> tuple[int, str | None]:
            try:
                result = self.client.generate(messages, json_schema=json_schema)
                return idx, result
            except Exception as e:
                self.logger.error(f"Request failed (id={idx}): {e}")
                return idx, None

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [
                executor.submit(run_single, idx, msgs)
                for idx, msgs in enumerate(messages_list)
            ]

            for future in tqdm(as_completed(futures), total=len(futures), desc=desc):
                try:
                    idx, result = future.result()
                    responses[idx] = result
                except Exception:
                    self.logger.exception("Worker crashed unexpectedly in threadpool")

        return responses

    def generate_embedding_from_input(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings from a list of texts.

        Args:
            texts: List of text strings to embed

        Returns:
            List of embedding vectors
        """
        responses = [None] * len(texts)

        def run_single(idx: int, text: str) -> tuple[int, list[float] | None]:
            try:
                result = self.client.generate_embedding(text)
                return idx, result
            except Exception as e:
                self.logger.error(f"Embedding request failed (id={idx}): {e}")
                return idx, None

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [
                executor.submit(run_single, idx, txt) for idx, txt in enumerate(texts)
            ]

            for future in tqdm(
                as_completed(futures),
                total=len(futures),
                desc="Generating embedding......",
            ):
                try:
                    idx, result = future.result()
                    responses[idx] = result
                except Exception:
                    self.logger.exception("Worker crashed unexpectedly in threadpool")

        return responses

    def cleanup(self):
        """Clean up resources."""
        self.logger.info("Cleaning up resources in APILLMServing_request")
        # No explicit cleanup needed for LLMClientAdapter
