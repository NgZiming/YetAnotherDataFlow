import json
import warnings
import requests
from requests.adapters import HTTPAdapter
import os
import logging
import base64
from typing import Any, Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from dataflow.core import LLMServingABC
import re
import time

from ..logger import get_logger
from dataflow.utils.storage import MediaStorage


class APIVLMServing_request(LLMServingABC):
    """Client for interacting with a Vision-Language Model (VLM) via vllm's OpenAI-compatible API.

    Uses requests library with connection pooling, detailed timeout handling, and retry mechanisms.
    Supports single-image and multi-image chat completions.
    """

    def start_serving(self) -> None:
        self.logger.info("APIVLMServing_request: no local service to start.")
        return

    def __init__(
        self,
        media_storage: MediaStorage,
        api_url: str = "http://localhost:8000/v1/chat/completions",
        key_name_of_api_key: str = "DF_API_KEY",
        model_name: str = "Qwen/Qwen2.5-VL-72B-Instruct",
        temperature: float = 0.0,
        max_workers: int = 10,
        max_retries: int = 5,
        connect_timeout: float = 10.0,
        read_timeout: float = 120.0,
        **configs: dict,
    ):
        """Initialize the VLM client with requests-based HTTP handling.

        :param media_storage: MediaStorage instance for reading image files.
        :param api_url: Full API endpoint URL (e.g., "http://localhost:8000/v1/chat/completions").
        :param key_name_of_api_key: Environment variable name for the API key.
        :param model_name: Default model name for requests.
        :param temperature: Sampling temperature.
        :param max_workers: Maximum concurrent threads.
        :param max_retries: Maximum retry attempts with exponential backoff.
        :param connect_timeout: Connection timeout in seconds.
        :param read_timeout: Read timeout in seconds.
        :param configs: Additional config parameters (will be merged into request payload).
        """
        self.media_storage = media_storage
        self.api_url = api_url
        self.model_name = model_name
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.temperature = temperature

        self.timeout = (connect_timeout, read_timeout)

        # Handle deprecated 'timeout' parameter
        if "timeout" in configs:
            warnings.warn(
                "The `timeout` parameter is deprecated. Please use `connect_timeout` and `read_timeout` instead.",
                DeprecationWarning,
            )
            self.timeout = (connect_timeout, configs["timeout"])
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

        # Initialize requests session with connection pool
        self.session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=self.max_workers,
            pool_maxsize=self.max_workers,
            max_retries=0,  # Don't retry here, we have _api_chat_id_retry
            pool_block=True,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Apifox/1.0.0 (https://apifox.com)",
        }

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

    def format_response(self, response: dict, is_embedding: bool = False) -> str:
        """Format API response, supporting think/answer tags and reasoning_content.

        :param response: API response dict.
        :param is_embedding: Whether this is an embedding response.
        :return: Formatted response string.
        """
        if is_embedding:
            return response.get("data", [{}])[0].get("embedding", [])

        # Extract message content
        message = response.get("choices", [{}])[0].get("message", {})
        content = message.get("content", "")

        # Return directly if already in think/answer format
        if re.search(
            r"<\|think\|>.*?</\|think\|>.*?<\|answer\|>.*?</\|answer\|>",
            content,
            re.DOTALL,
        ):
            return content

        # Check for reasoning_content
        reasoning_content = message.get("reasoning_content")

        # Wrap with think/answer tags if reasoning_content exists
        if reasoning_content:
            return f"<|think|>{reasoning_content}</\|think\|>\n<|answer|>{content}</\|answer\|>"

        return content

    def _api_chat_with_id(
        self,
        id: int,
        payload: List[Dict[str, Any]],
        model: str,
        json_schema: dict = None,
    ) -> Tuple[int, str]:
        """Send a single chat completion request.

        :param id: Request identifier for tracking.
        :param payload: Messages payload (list of message dicts).
        :param model: Model name for this request.
        :param json_schema: Optional JSON schema for structured output.
        :return: Tuple of (id, response content) or (id, None) on failure.
        """
        start = time.time()
        try:
            # Build request payload
            if json_schema is None:
                request_payload = {"model": model, "messages": payload}
            else:
                request_payload = {
                    "model": model,
                    "messages": payload,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "custom_response",
                            "strict": True,
                            "schema": json_schema,
                        },
                    },
                }

            request_payload.update(self.configs)
            request_json = json.dumps(request_payload)

            # Send request
            response = self.session.post(
                self.api_url,
                headers=self.headers,
                data=request_json,
                timeout=self.timeout,
            )
            cost = time.time() - start

            if response.status_code == 200:
                response_data = response.json()
                return id, self.format_response(response_data)
            else:
                self.logger.error(
                    f"API request failed id={id} status={response.status_code} cost={cost:.2f}s body={response.text[:500]}"
                )
                return id, None

        except requests.exceptions.ConnectTimeout as e:
            cost = time.time() - start
            self.logger.error(f"API connect timeout (id={id}) cost={cost:.2f}s: {e}")
            raise RuntimeError(
                f"Cannot connect to VLM server (connect timeout): {e}"
            ) from e

        except requests.exceptions.ReadTimeout as e:
            cost = time.time() - start
            warnings.warn(
                f"API read timeout (id={id}) cost={cost:.2f}s: {e}", RuntimeWarning
            )
            return id, None

        except requests.exceptions.Timeout as e:
            cost = time.time() - start
            warnings.warn(
                f"API timeout (id={id}) cost={cost:.2f}s: {e}", RuntimeWarning
            )
            return id, None

        except requests.exceptions.ConnectionError as e:
            cost = time.time() - start
            msg = str(e).lower()

            # Check if it's actually a read timeout wrapped as ConnectionError
            if "read timed out" in msg:
                warnings.warn(
                    f"API read timeout (id={id}) cost={cost:.2f}s: {e}", RuntimeWarning
                )
                return id, None

            # Check for connect timeout
            if "connect timeout" in msg or ("timed out" in msg and "connect" in msg):
                self.logger.error(
                    f"API connect timeout (id={id}) cost={cost:.2f}s: {e}"
                )
                raise RuntimeError(
                    f"Cannot connect to VLM server (connect timeout): {e}"
                ) from e

            # Other connection errors
            self.logger.error(f"API connection error (id={id}) cost={cost:.2f}s: {e}")
            raise RuntimeError(f"Cannot connect to VLM server: {e}") from e

        except Exception as e:
            cost = time.time() - start
            self.logger.exception(f"API request error (id={id}) cost={cost:.2f}s: {e}")
            return id, None

    def _api_chat_id_retry(
        self,
        id: int,
        payload: List[Dict[str, Any]],
        model: str,
        json_schema: dict = None,
    ) -> Tuple[int, str]:
        """Send request with exponential backoff retry.

        :param id: Request identifier.
        :param payload: Messages payload.
        :param model: Model name.
        :param json_schema: Optional JSON schema.
        :return: Tuple of (id, response) or (id, None) after all retries exhausted.
        """
        for i in range(self.max_retries):
            id, response = self._api_chat_with_id(id, payload, model, json_schema)
            if response is not None:
                return id, response
            time.sleep(2**i)

        return id, None

    def _run_threadpool(self, task_args_list: List[dict], desc: str) -> List[str]:
        """Execute multiple requests concurrently using thread pool.

        :param task_args_list: List of kwargs dicts for _api_chat_id_retry.
        :param desc: Progress bar description.
        :return: List of responses ordered by id.
        """
        responses = [None] * len(task_args_list)

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [
                executor.submit(self._api_chat_id_retry, **task_args)
                for task_args in task_args_list
            ]

            for future in tqdm(as_completed(futures), total=len(futures), desc=desc):
                try:
                    result = future.result()  # (id, response)
                    responses[result[0]] = result[1]
                except Exception:
                    self.logger.exception("Worker crashed unexpectedly in threadpool")

        return responses

    def chat_with_one_image(
        self,
        image_path: str,
        text_prompt: str,
        model: str = None,
        json_schema: dict = None,
    ) -> str:
        """Send a chat request with a single image.

        :param image_path: Path to the image file.
        :param text_prompt: Text prompt to accompany the image.
        :param model: Model override (defaults to instance model_name).
        :param json_schema: Optional JSON schema for structured output.
        :return: Model response as string.
        """
        model = model or self.model_name
        b64, fmt = self._encode_image_to_base64(image_path)

        # Build VLM-compatible message content
        content = [
            {"type": "text", "text": text_prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/{fmt};base64,{b64}"},
            },
        ]

        payload = [{"role": "user", "content": content}]
        _, response = self._api_chat_id_retry(0, payload, model, json_schema)
        return response

    def chat_with_one_image_with_id(
        self,
        request_id: Any,
        image_path: str,
        text_prompt: str,
        model: str = None,
        json_schema: dict = None,
    ) -> Tuple[Any, str]:
        """Same as chat_with_one_image but returns (request_id, response).

        :param request_id: Identifier for tracking.
        :param image_path: Path to the image file.
        :param text_prompt: Text prompt.
        :param model: Model override.
        :param json_schema: Optional JSON schema.
        :return: Tuple of (request_id, response).
        """
        model = model or self.model_name
        b64, fmt = self._encode_image_to_base64(image_path)

        content = [
            {"type": "text", "text": text_prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/{fmt};base64,{b64}"},
            },
        ]

        payload = [{"role": "user", "content": content}]
        _, response = self._api_chat_id_retry(request_id, payload, model, json_schema)
        return request_id, response

    def generate_from_input(
        self,
        user_inputs: List[str],
        system_prompt: str = "Describe the image in detail.",
        json_schema: dict = None,
    ) -> List[str]:
        """Batch process single-image chat requests.

        :param user_inputs: List of image paths.
        :param system_prompt: System prompt prepended to each user prompt.
        :param json_schema: Optional JSON schema for structured output.
        :return: List of responses in input order.
        """
        task_args_list = []
        for idx, image_path in enumerate(user_inputs):
            b64, fmt = self._encode_image_to_base64(image_path)
            task_args_list.append(
                dict(
                    id=idx,
                    payload=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": system_prompt,
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/{fmt};base64,{b64}"
                                    },
                                },
                            ],
                        }
                    ],
                    model=self.model_name,
                    json_schema=json_schema,
                )
            )

        return self._run_threadpool(
            task_args_list, desc="Generating VLM responses......"
        )

    def generate_from_input_one_image(
        self,
        image_paths: List[str],
        text_prompts: List[str],
        system_prompt: str = "",
        model: str = None,
        json_schema: dict = None,
    ) -> List[str]:
        """Batch process single-image chat requests with separate prompts.

        :param image_paths: List of image file paths.
        :param text_prompts: List of text prompts (must match length of image_paths).
        :param system_prompt: Optional system-level prompt.
        :param model: Model override.
        :param json_schema: Optional JSON schema.
        :return: List of responses in input order.
        :raises ValueError: If lengths don't match.
        """
        if len(image_paths) != len(text_prompts):
            raise ValueError(
                "`image_paths` and `text_prompts` must have the same length"
            )

        model = model or self.model_name
        prompts = [f"{system_prompt}\n{p}" for p in text_prompts]

        task_args_list = [
            dict(
                id=idx,
                payload=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompts[idx]},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/{self._encode_image_to_base64(image_paths[idx])[1]};base64,{self._encode_image_to_base64(image_paths[idx])[0]}"
                                },
                            },
                        ],
                    }
                ],
                model=model,
                json_schema=json_schema,
            )
            for idx in range(len(image_paths))
        ]
        return self._run_threadpool(
            task_args_list, desc="Generating VLM responses......"
        )

    def analyze_images_with_gpt(
        self,
        image_paths: List[str],
        image_labels: List[str],
        system_prompt: str = "",
        model: str = None,
        json_schema: dict = None,
    ) -> str:
        """Analyze multiple images in a single request with labels.

        :param image_paths: List of image file paths.
        :param image_labels: Corresponding labels for each image.
        :param system_prompt: Overall prompt before listing images.
        :param model: Model override.
        :param json_schema: Optional JSON schema.
        :return: Combined analysis as text.
        :raises ValueError: If lengths don't match.
        """
        if len(image_paths) != len(image_labels):
            raise ValueError(
                "`image_paths` and `image_labels` must have the same length"
            )

        model = model or self.model_name

        # Build content with system prompt and image-label pairs
        content = []
        if system_prompt:
            content.append({"type": "text", "text": system_prompt})

        for label, path in zip(image_labels, image_paths):
            b64, fmt = self._encode_image_to_base64(path)
            content.append({"type": "text", "text": f"{label}:"})
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/{fmt};base64,{b64}"},
                }
            )

        payload = [{"role": "user", "content": content}]
        _, response = self._api_chat_id_retry(0, payload, model, json_schema)
        return response

    def analyze_images_with_gpt_with_id(
        self,
        request_id: Any,
        image_paths: List[str],
        image_labels: List[str],
        system_prompt: str = "",
        model: str = None,
        json_schema: dict = None,
    ) -> Tuple[Any, str]:
        """Batch-tracked version of analyze_images_with_gpt.

        :param request_id: Identifier for tracking.
        :param image_paths: List of image file paths.
        :param image_labels: Corresponding labels.
        :param system_prompt: Overall prompt.
        :param model: Model override.
        :param json_schema: Optional JSON schema.
        :return: Tuple of (request_id, analysis).
        """
        model = model or self.model_name

        content = []
        if system_prompt:
            content.append({"type": "text", "text": system_prompt})

        for label, path in zip(image_labels, image_paths):
            b64, fmt = self._encode_image_to_base64(path)
            content.append({"type": "text", "text": f"{label}:"})
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/{fmt};base64,{b64}"},
                }
            )

        payload = [{"role": "user", "content": content}]
        _, response = self._api_chat_id_retry(request_id, payload, model, json_schema)
        return request_id, response

    def generate_from_input_multi_images(
        self,
        list_of_image_paths: List[List[str]],
        list_of_image_labels: List[List[str]],
        system_prompt: str = "",
        user_prompts: List[str] = None,
        model: str = None,
        json_schema: dict = None,
    ) -> List[str]:
        """Concurrently analyze multiple sets of images with labels.

        :param list_of_image_paths: List of image path lists.
        :param list_of_image_labels: Parallel list of label lists.
        :param system_prompt: Prompt prefixed to each batch.
        :param user_prompts: Optional per-batch user prompts.
        :param model: Model override.
        :param json_schema: Optional JSON schema.
        :return: List of analysis results in input order.
        :raises ValueError: If outer list lengths differ.
        """
        if len(list_of_image_paths) != len(list_of_image_labels):
            raise ValueError(
                "`list_of_image_paths` and `list_of_image_labels` must have the same length"
            )

        model = model or self.model_name

        if user_prompts is None:
            user_prompts = [""] * len(list_of_image_paths)

        task_args_list = []
        for idx, (paths, labels, user_prompt) in enumerate(
            zip(list_of_image_paths, list_of_image_labels, user_prompts)
        ):
            content = []
            if system_prompt:
                content.append({"type": "text", "text": system_prompt})
            if user_prompt:
                content.append({"type": "text", "text": user_prompt})

            for label, path in zip(labels, paths):
                b64, fmt = self._encode_image_to_base64(path)
                content.append({"type": "text", "text": f"{label}:"})
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/{fmt};base64,{b64}"},
                    }
                )

            task_args_list.append(
                dict(
                    id=idx,
                    payload=[{"role": "user", "content": content}],
                    model=model,
                    json_schema=json_schema,
                )
            )

        return self._run_threadpool(
            task_args_list, desc="Generating VLM responses......"
        )

    def cleanup(self):
        """Clean up resources (close requests session)."""
        self.logger.info("Cleaning up resources in APIVLMServing_request")
        try:
            if hasattr(self, "session") and self.session:
                self.session.close()
        except Exception:
            self.logger.exception("Failed to close requests session")
