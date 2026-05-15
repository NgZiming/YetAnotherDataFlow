import base64
import os
import re
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple, Optional

from tqdm import tqdm

from dataflow.core import LLMServingABC
from dataflow.logger import get_logger
from dataflow.utils.storage import MediaStorage
from .llm_client import LLMClientAdapter


class APIVLMServing_request(LLMServingABC):
    """Client for interacting with a Vision-Language Model (VLM) via vllm's OpenAI-compatible API.

    Uses LLMClientAdapter for HTTP communication.
    Supports single-image and multi-image chat completions.
    """

    def start_serving(self) -> None:
        self.logger.info("APIVLMServing_request: no local service to start.")
        return

    def __init__(
        self,
        media_storage: MediaStorage,
        api_url: str = "http://localhost:8000/v1",
        key_name_of_api_key: str = "DF_API_KEY",
        model_name: str = "Qwen/Qwen2.5-VL-72B-Instruct",
        temperature: float = 0.0,
        max_workers: int = 10,
        max_retries: int = 5,
        read_timeout: float = 120.0,
        **configs: dict,
    ):
        """Initialize the VLM client.

        :param media_storage: MediaStorage instance for reading image files.
        :param api_url: The API base URL (e.g., "http://localhost:8000/v1").
        :param key_name_of_api_key: Environment variable name for API key.
        :param model_name: Default model name for requests.
        :param temperature: Sampling temperature.
        :param max_workers: Maximum concurrent threads.
        :param max_retries: Maximum retry attempts.
        :param read_timeout: Read timeout in seconds.
        :param configs: Additional config parameters.
        """
        self.media_storage = media_storage
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
            error_msg = f"Lack of `{key_name_of_api_key}` in environment variables. Please set `{key_name_of_api_key}` as your api-key to {api_url} before using APIVLMServing_request."
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
            max_retries=self.max_retries,
        )

    def _encode_image_to_base64(self, image_path: str) -> Tuple[str, str]:
        """Read an image file and convert it to base64-encoded string.

        :param image_path: Path to the image file.
        :return: Tuple of (base64 string, MIME format like 'jpeg' or 'png').
        :raises ValueError: If image format is unsupported.
        """
        raw = self.media_storage.read_media_bytes(image_path)
        b64 = base64.b64encode(raw).decode("utf-8")
        ext = image_path.rsplit(".", 1)[-1].lower()

        if ext in ("jpg", "jpeg"):
            fmt = "jpeg"
        elif ext == "png":
            fmt = "png"
        else:
            raise ValueError(f"Unsupported image format: {ext}")

        return b64, fmt

    def format_response(self, response: str) -> str:
        """Format response, supporting think/answer tags and reasoning_content.

        :param response: API response string.
        :return: Formatted response string.
        """
        # Return directly if already in think/answer format
        if re.search(
            r"<\|think\|>.*?</\|think\|>.*?<\|answer\|>.*?</\|answer\|>",
            response,
            re.DOTALL,
        ):
            return response

        # Check for reasoning_content pattern (if present in response)
        # Note: LLMClientAdapter already extracts content, so this handles edge cases
        return response

    def _run_batch(
        self,
        messages_list: List[List[Dict[str, Any]]],
        desc: str,
        json_schema: Optional[dict] = None,
    ) -> List[Optional[str]]:
        """Execute multiple requests concurrently using thread pool.

        :param messages_list: List of message lists for each request.
        :param desc: Progress bar description.
        :param json_schema: Optional JSON schema for structured output.
        :return: List of responses ordered by id.
        """
        responses: List[Optional[str]] = [None] * len(messages_list)

        def run_single(
            idx: int, messages: List[Dict[str, Any]]
        ) -> Tuple[int, Optional[str]]:
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

    def generate_from_input(
        self,
        user_inputs: List[str],
        system_prompt: str = "Describe the image in detail.",
        json_schema: Optional[dict] = None,
    ) -> List[Optional[str]]:
        """Batch process single-image chat requests.

        :param user_inputs: List of image paths.
        :param system_prompt: System prompt prepended to each user prompt.
        :param json_schema: Optional JSON schema for structured output.
        :return: List of responses in input order.
        """
        messages_list = []
        for image_path in user_inputs:
            b64, fmt = self._encode_image_to_base64(image_path)
            messages_list.append(
                [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": system_prompt,
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/{fmt};base64,{b64}"},
                            },
                        ],
                    }
                ]
            )

        return self._run_batch(
            messages_list,
            desc="Generating VLM responses......",
            json_schema=json_schema,
        )

    def generate_from_input_one_image(
        self,
        image_paths: List[str],
        text_prompts: List[str],
        system_prompt: str = "",
        json_schema: Optional[dict] = None,
    ) -> List[Optional[str]]:
        """Batch process single-image chat requests with separate prompts.

        :param image_paths: List of image file paths.
        :param text_prompts: List of text prompts (must match length of image_paths).
        :param system_prompt: Optional system-level prompt.
        :param model: Model override (not used, model is set in client_params).
        :param json_schema: Optional JSON schema.
        :return: List of responses in input order.
        :raises ValueError: If lengths don't match.
        """
        if len(image_paths) != len(text_prompts):
            raise ValueError(
                "`image_paths` and `text_prompts` must have the same length"
            )

        prompts = [f"{system_prompt}\n{p}" for p in text_prompts]

        # Pre-encode all images to avoid repeated reads
        encoded_images = [self._encode_image_to_base64(ip) for ip in image_paths]

        messages_list = [
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompts[idx]},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/{fmt};base64,{b64}"},
                        },
                    ],
                }
            ]
            for idx, (fmt, b64) in enumerate(encoded_images)
        ]

        return self._run_batch(
            messages_list,
            desc="Generating VLM responses......",
            json_schema=json_schema,
        )

    def cleanup(self):
        """Clean up resources."""
        self.logger.info("Cleaning up resources in APIVLMServing_request")
        # No explicit cleanup needed for LLMClientAdapter
