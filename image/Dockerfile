# Runner image for Render sandboxes: Ubuntu 24.04, Docker Engine, and the
# GitHub Actions runner. Render imports it with `images.import_` and starts
# sandboxes from it. Sandboxes ignore ENTRYPOINT/CMD; the controller starts
# /usr/local/bin/start-runner through exec.
FROM ubuntu:24.04

ARG RUNNER_VERSION=2.338.2
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential ca-certificates curl git gnupg iproute2 iptables jq \
      lsb-release python3 python3-venv sudo unzip zip \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc \
 && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" \
      > /etc/apt/sources.list.d/docker.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
 && rm -rf /var/lib/apt/lists/*

# Jobs run as `runner`, with passwordless sudo like GitHub-hosted runners.
RUN useradd -m -s /bin/bash runner \
 && usermod -aG docker,sudo runner \
 && echo "runner ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/runner \
 && chmod 0440 /etc/sudoers.d/runner

WORKDIR /home/runner
RUN curl -fsSL -o runner.tgz \
      "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz" \
 && tar xzf runner.tgz \
 && rm runner.tgz \
 && ./bin/installdependencies.sh \
 && rm -rf /var/lib/apt/lists/* \
 && chown -R runner:runner /home/runner

COPY start-runner.sh /usr/local/bin/start-runner
RUN chmod 0755 /usr/local/bin/start-runner
