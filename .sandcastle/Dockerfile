FROM node:22-bookworm

# Install system dependencies
RUN apt-get update && apt-get install -y \
  git \
  curl \
  jq \
  && rm -rf /var/lib/apt/lists/*

# Install GitHub CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
  && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
  && apt-get update && apt-get install -y gh \
  && rm -rf /var/lib/apt/lists/*

# Install the swift.org Linux toolchain (ADR-0040) alongside uv. Only the pure-Swift
# BlindfoldCore SwiftPM library (macos/BlindfoldCore) builds and unit-tests here; the
# AppKit .app / signing / notarization are macOS-only and gated on the self-hosted mac
# runner — never built in this Linux sandbox. Additive: the base image stays node:22-bookworm.
# Runs while still root (needs apt + /opt); pinned + GPG-verified for a reproducible image.
ARG SWIFT_VERSION=6.3.3
RUN set -eux; \
  apt-get update; \
  apt-get install -y --no-install-recommends \
    binutils-gold gcc git gnupg2 libcurl4-openssl-dev libedit-dev libicu-dev \
    libncurses-dev libpython3-dev libsqlite3-dev libxml2-dev pkg-config \
    tzdata uuid-dev; \
  arch="$(dpkg --print-architecture)"; \
  case "$arch" in \
    amd64) swift_platform=debian12 ;; \
    arm64) swift_platform=debian12-aarch64 ;; \
    *) echo "unsupported arch for Swift toolchain: $arch" >&2; exit 1 ;; \
  esac; \
  base="https://download.swift.org/swift-${SWIFT_VERSION}-release/${swift_platform}/swift-${SWIFT_VERSION}-RELEASE"; \
  tarball="swift-${SWIFT_VERSION}-RELEASE-${swift_platform}.tar.gz"; \
  curl -fsSL --compressed -o /tmp/swift.tar.gz "${base}/${tarball}"; \
  curl -fsSL --compressed -o /tmp/swift.tar.gz.sig "${base}/${tarball}.sig"; \
  # --compressed is required: the swift.org CDN serves all-keys.asc with
  # Content-Encoding: gzip, so without it curl writes gzipped bytes and gpg rejects
  # them ("no valid OpenPGP data found"). Fetch to a file so a failed download aborts
  # here rather than piping empty stdin into gpg.
  curl -fsSL --compressed -o /tmp/swift-keys.asc https://swift.org/keys/all-keys.asc; \
  gpg --import /tmp/swift-keys.asc; \
  gpg --batch --verify /tmp/swift.tar.gz.sig /tmp/swift.tar.gz; \
  mkdir -p /opt/swift; \
  tar -xzf /tmp/swift.tar.gz -C /opt/swift --strip-components=1; \
  rm -rf /tmp/swift.tar.gz /tmp/swift.tar.gz.sig /tmp/swift-keys.asc /var/lib/apt/lists/*
ENV PATH="/opt/swift/usr/bin:$PATH"

# Build-args for UID/GID alignment: sandcastle docker build-image
# defaults these to the host user's UID/GID so image-built files
# and bind-mounted files share an owner without runtime chown.
ARG AGENT_UID=1000
ARG AGENT_GID=1000

# Chromium's system libraries for headless browser tests (Playwright). Needs
# root + apt, so it runs while we are still root; the browser *binary* is
# downloaded per-user further down. Only exercised once the management SPA
# (ADR-0011) exists — harmless to bake in ahead of time.
RUN npx -y playwright@latest install-deps chromium

# Rename the base image's "node" user to "agent" and align UID/GID.
RUN groupmod -o -g $AGENT_GID node && usermod -o -u $AGENT_UID -g $AGENT_GID -d /home/agent -m -l agent node
USER ${AGENT_UID}:${AGENT_GID}

# Install Claude Code CLI
RUN curl -fsSL https://claude.ai/install.sh | bash

# Install uv — the Python package/project manager. Blindfold is a Python/FastAPI
# project (pyproject.toml, requires-python >=3.12); uv provisions the right Python
# itself on `uv sync`, so we don't need a separate python apt package.
RUN curl -fsSL https://astral.sh/uv/install.sh | sh

# Add Claude + uv (both land in ~/.local/bin) to PATH
ENV PATH="/home/agent/.local/bin:$PATH"

# Bound uv's per-request HTTP timeout so a stalled read from PyPI's CDN aborts and
# retries instead of hanging the (synchronous) `uv sync` sandbox-setup hook. This
# is the socket-layer first line of defence; main.mts also caps each `uv sync`
# attempt with a hard `timeout` + retry as the guarantee.
ENV UV_HTTP_TIMEOUT=60

# Pre-provision the interpreter the project requires so the first `uv sync` is fast.
RUN /home/agent/.local/bin/uv python install 3.12

# Download the Chromium browser binary for the agent user (system libs installed
# as root above). Lands in /home/agent/.cache/ms-playwright. Used by the
# web-verify stage's scripted Playwright tests once the SPA exists.
RUN npx -y playwright@latest install chromium

WORKDIR /home/agent

# In worktree sandbox mode, Sandcastle bind-mounts the git worktree at /home/agent/workspace
# and overrides the working directory to /home/agent/workspace at container start.
# Structure your Dockerfile so that /home/agent/workspace can serve as the project root.
ENTRYPOINT ["sleep", "infinity"]
