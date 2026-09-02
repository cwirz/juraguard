FROM ubuntu:latest

RUN apt-get update && apt-get install -y \
    wget \
    curl \
    jq \
    ssh \
    python3 \
    python3-pip \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN wget https://github.com/hetznercloud/cli/releases/download/v1.46.0/hcloud-linux-amd64.tar.gz -O hcloud.tar.gz \
    && tar -xvzf ./hcloud.tar.gz \
    && mv hcloud /usr/local/bin/hcloud \
    && rm hcloud.tar.gz

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

ENV HCLOUD_TOKEN=""
