#!/bin/bash

# Initialize OpenClaw configuration if not exists
if [ ! -d "$HOME/.openclaw" ]; then
    echo "Initializing OpenClaw configuration..."

    # Create basic workspace structure
    mkdir -p "$HOME/.openclaw/workspace"

    openclaw config set models.providers.vllm << EOF
{
  "baseUrl": "${VLLM_BASE_URL:-http://xxxxxxxxxx:8000/v1}",
  "apiKey": "${VLLM_API_KEY}",
  "api": "openai-completions",
  "models": [
    {
      "id": "${VLLM_MODEL_ID:-xxxxxxx}",
      "name": "${VLLM_MODEL_NAME:-xxxxxxx}",
      "reasoning": false,
      "input": ["text"],
      "cost": {
        "input": 0,
        "output": 0,
        "cacheRead": 0,
        "cacheWrite": 0
      },
      "contextWindow": 128000,
      "maxTokens": 8192
    }
  ]
}
EOF
    openclaw models set ${VLLM_MODEL_ID:-vllm/MY_MODEL}
    openclaw config set gateway.mode local

    # Create auth profiles for vllm provider
    mkdir -p "$HOME/.openclaw/agents/main/agent"
    cat > "$HOME/.openclaw/agents/main/agent/auth-profiles.json" << EOF
{
  "version": 1,
  "profiles": {
    "vllm:default": {
      "type": "api_key",
      "provider": "vllm",
      "key": "${VLLM_API_KEY}"
    }
  },
  "lastGood": {
    "vllm": "vllm:default"
  },
  "usageStats": {
    "vllm:default": {
      "errorCount": 0,
      "lastUsed": $(date +%s%3N)
    }
  }
}
EOF

    openclaw gateway install

    echo "OpenClaw configuration initialized."
fi

# Start OpenClaw gateway (loopback only)
echo "Starting OpenClaw gateway..."
openclaw gateway start

# Activate dataflow environment and keep container running
source /opt/miniconda/etc/profile.d/conda.sh
conda activate dataflow

exec "$@"
