# GitHub Actions self-hosted runner with multi-architecture support
FROM ubuntu:22.04

# Arguments for configurability
ARG RUNNER_VERSION=2.323.0
ARG DEBIAN_FRONTEND=noninteractive
ARG TARGETPLATFORM

# Install dependencies
RUN apt-get update && apt-get install -y \
    curl \
    tar \
    jq \
    sudo \
    git \
    build-essential \
    libssl-dev \
    libffi-dev \
    python3 \
    python3-pip \
    nodejs \
    npm \
    ca-certificates \
    wget \
    unzip \
    apt-transport-https \
    software-properties-common \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create a runner user
RUN useradd -m runner && \
    usermod -aG sudo runner && \
    echo "runner ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

WORKDIR /home/runner

# Download the appropriate runner based on architecture
# This will detect the platform during build time and download the correct runner
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then \
        RUNNER_ARCH="x64"; \
    elif [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then \
        RUNNER_ARCH="arm64"; \
    else \
        echo "Unsupported architecture: $ARCH" && exit 1; \
    fi && \
    curl -o actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz -L https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz && \
    tar xzf actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz && \
    rm actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz && \
    chown -R runner:runner /home/runner

# Install Docker CLI inside the container (to allow Docker operations if needed)
RUN curl -fsSL https://get.docker.com -o get-docker.sh && \
    sh get-docker.sh && \
    usermod -aG docker runner && \
    rm get-docker.sh

# Copy startup script
COPY start.sh /home/runner/start.sh
RUN chmod +x /home/runner/start.sh && \
    chown runner:runner /home/runner/start.sh

# Install additional dependencies that might be needed for different architectures
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then \
        # ARM64 specific dependencies if needed
        echo "Installing ARM64 specific dependencies"; \
    elif [ "$ARCH" = "x86_64" ]; then \
        # x64 specific dependencies if needed
        echo "Installing x64 specific dependencies"; \
    fi

USER runner

# Start the runner
ENTRYPOINT ["/home/runner/start.sh"]