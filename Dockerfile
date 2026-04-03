# Dockerfile with Miniconda and Python 3.12 environment
FROM ubuntu:24.04

USER root
ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    git \
    bzip2 \
    libreoffice \
    ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 24 via NodeSource
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - && \
    apt-get install -y nodejs && \
    node -v && npm -v && \
    npm install -g openclaw && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Miniconda
RUN wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /tmp/miniconda.sh && \
    bash /tmp/miniconda.sh -b -p /opt/miniconda && \
    rm /tmp/miniconda.sh && \
    /opt/miniconda/bin/conda init bash

ENV PATH="/opt/miniconda/bin:${PATH}"

# Create Python 3.12 environment
RUN conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
RUN conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r
RUN conda create -n dataflow python=3.12 -y

SHELL ["/bin/bash", "-c"]

RUN source activate dataflow && pip config set global.index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple

# Copy local dataflow code into container
COPY . .

RUN source activate dataflow && pip install --no-cache-dir -e .

WORKDIR /workspace

# Copy and setup entrypoint script
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Put your skills here
# COPY skills /root/skills

# Set entrypoint for OpenClaw initialization
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
