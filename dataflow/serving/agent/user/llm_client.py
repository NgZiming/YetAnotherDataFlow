from typing import Any, Dict, Optional
import requests
from dataflow.core.agentic import LLMClientABC


class LLMClientAdapter(LLMClientABC):
    """
    Standard LLM Client that handles the REST communication with the User Simulator LLM.
    """

    def __init__(
        self,
        api_url: str,
        api_key: Optional[str] = None,
        client_params: Optional[Dict[str, Any]] = None,
    ):
        self.api_url = api_url
        self.api_key = api_key
        self.client_params = {
            "model": "/data/share/models/Qwen3.5-122B-A10B/",
            "max_workers": 10,
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

    async def generate(
        self, prompt: str, config: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Perform a synchronous HTTP POST request to the LLM API and return the generated text.
        """
        # Merge configs
        current_config = self.client_params.copy()
        if config:
            current_config.update(config)

        # Build payload
        payload = {
            "model": current_config.get("model", "default"),
            "messages": [{"role": "user", "content": prompt}],
        }
        payload.update(current_config)

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
                self.api_url,
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
