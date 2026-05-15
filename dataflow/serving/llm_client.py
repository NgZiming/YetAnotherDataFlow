from typing import Any, Dict, List, Optional, Union
import requests
from dataflow.core.agentic import LLMClientABC, MessagesType


class LLMClientAdapter(LLMClientABC):
    """
    Standard LLM Client that handles REST communication with support for both
    text-only and multimodal (text + image) inputs.

    This adapter can be used by:
    - User Simulator (via async interface)
    - APILLMServing_request (as underlying client)
    - APIVLMServing_request (as underlying client with image support)

    Note: This client does NOT handle image encoding. Callers are responsible for
    preparing the complete messages format, including base64-encoded images if needed.
    """

    def __init__(
        self,
        api_url: str = "http://localhost:8000/v1",
        api_key: Optional[str] = None,
        client_params: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize the LLM client.

        Args:
            api_url: The API endpoint URL
            api_key: Optional API key for authentication
            client_params: Default parameters for LLM requests (model, temperature, etc.)
        """
        self.api_url = api_url
        self.api_key = api_key

        # Default client parameters
        self.client_params = {
            "model": "/data/share/models/Qwen3.5-122B-A10B/",
            "max_completion_tokens": 16384,
            "read_timeout": 600,
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 1.5,
            "repetition_penalty": 1.0,
            "chat_template_kwargs": {
                "enable_thinking": False,
            },
        }
        if client_params:
            self.client_params.update(client_params)

    def _normalize_messages(
        self,
        prompt: MessagesType,
    ) -> List[Dict[str, Any]]:
        """
        Normalize input to a standard messages format.

        Args:
            prompt: Can be:
                - A plain text string
                - A list of message dicts (each must have "role" key)

        Returns:
            Standard messages list format

        Raises:
            TypeError: If prompt type is unsupported
            ValueError: If message structure is invalid
        """
        # Case 1: Plain text string
        if isinstance(prompt, str):
            return [{"role": "user", "content": prompt}]

        # Case 2: List of message dicts
        if isinstance(prompt, list):
            if not prompt:
                raise ValueError("Messages list cannot be empty")

            messages = []
            for item in prompt:
                if not isinstance(item, dict):
                    raise TypeError(f"Message item must be a dict, got {type(item)}")

                if "role" not in item:
                    raise ValueError(f"Message dict must have 'role' key, got: {item}")

                if item["role"] not in ("system", "user", "assistant"):
                    raise ValueError(
                        f"Invalid role: {item['role']}. Must be 'system', 'user', or 'assistant'"
                    )

                # Validate content field if present
                if "content" in item and not isinstance(item["content"], (str, list)):
                    raise ValueError(
                        f"Content must be str or list, got {type(item['content'])}"
                    )

                messages.append(item)

            return messages

        # Invalid type
        raise TypeError(
            f"Unsupported prompt type: {type(prompt)}. Expected str or list."
        )

    def generate(
        self,
        prompt: MessagesType,
        config: Optional[Dict[str, Any]] = None,
        json_schema: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Perform a synchronous HTTP POST request to the LLM API and return the generated text.
        Supports both text-only and multimodal (text + image) inputs.

        Args:
            prompt: Either a plain text string or a list of message dicts/content items
            config: Optional config overrides
            json_schema: Optional JSON schema for structured output

        Returns:
            Generated text response
        """
        # Merge configs
        current_config = self.client_params.copy()
        if config:
            current_config.update(config)

        # Normalize prompt to messages format
        messages = self._normalize_messages(prompt)

        # Build payload - exclude client-only params
        client_only_params = {"read_timeout"}
        payload = {
            "model": current_config.get("model", "default"),
            "messages": messages,
        }

        # Add config params that are valid for the API
        for key, value in current_config.items():
            if key not in client_only_params:
                payload[key] = value

        # Add json_schema for structured output if provided
        if json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "custom_response",
                    "strict": True,
                    "schema": json_schema,
                },
            }

        # Setup headers
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Request timeout
        request_timeout = current_config.get("read_timeout", 600)

        try:
            # Note: using requests.post synchronously inside an async method.
            # In a high-concurrency environment, consider using httpx or aiohttp.
            response = requests.post(
                self.api_url + "/chat/completions",
                headers=headers,
                json=payload,
                timeout=request_timeout,
            )

            if response.status_code != 200:
                raise Exception(
                    f"LLM Request failed: status={response.status_code}, body={response.text[:500]}"
                )

            result = response.json()
            return result.get("choices", [{}])[0].get("message", {}).get("content", "")

        except Exception as e:
            raise RuntimeError(f"LLM Client Error: {str(e)}") from e

    def generate_embedding(
        self,
        text: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> List[float]:
        """
        Generate embedding (vector representation) for a text input.

        Args:
            text: Input text to embed
            config: Optional config overrides (model, etc.)

        Returns:
            List of floats representing the embedding vector

        Raises:
            TypeError: If text is not a string
            RuntimeError: If embedding generation fails
        """
        if not isinstance(text, str):
            raise TypeError(f"Text must be a string, got {type(text)}")

        # Merge configs
        current_config = self.client_params.copy()
        if config:
            current_config.update(config)

        # Build payload - exclude client-only params
        client_only_params = {"read_timeout"}
        payload = {
            "model": current_config.get("model", "default"),
            "input": text,
        }

        # Add config params that are valid for the API
        for key, value in current_config.items():
            if key not in client_only_params:
                payload[key] = value

        # Setup headers
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Request timeout
        request_timeout = current_config.get("read_timeout", 600)

        try:
            response = requests.post(
                self.api_url + "/embeddings",
                headers=headers,
                json=payload,
                timeout=request_timeout,
            )

            if response.status_code != 200:
                raise Exception(
                    f"Embedding Request failed: status={response.status_code}, body={response.text[:500]}"
                )

            result = response.json()
            # Extract embedding from response (OpenAI API format)
            embedding = result.get("data", [{}])[0].get("embedding")
            if embedding is None:
                raise RuntimeError(
                    f"Embedding not found in response: {result.get('data')}"
                )
            return embedding

        except Exception as e:
            raise RuntimeError(f"Embedding Client Error: {str(e)}") from e
