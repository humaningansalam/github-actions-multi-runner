FROM ubuntu

# 기본 설정
ARG RUNNER_VERSION=2.323.0
ARG DEBIAN_FRONTEND=noninteractive
ARG DOCKER_HOST_GID=998
ENV TZ=Etc/UTC

# 필수 패키지 및 Docker CLI 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl tar jq git ca-certificates wget unzip gnupg lsb-release \
    apt-transport-https software-properties-common sudo \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Docker 그룹 설정
RUN groupadd -g ${DOCKER_HOST_GID} docker || grep -q "^docker:" /etc/group || groupadd docker

# runner 사용자 설정
RUN useradd -m -s /bin/bash runner \
    && usermod -aG sudo runner \
    && usermod -aG docker runner \
    && echo "runner ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# 러너 설치 및 설정
WORKDIR /home/runner
RUN ARCH=$(dpkg --print-architecture) && \
    case ${ARCH} in \
        amd64) RUNNER_ARCH="x64" ;; \
        arm64) RUNNER_ARCH="arm64" ;; \
        *) echo "Unsupported architecture: ${ARCH}" ; exit 1 ;; \
    esac && \
    curl -o actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz -L https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz && \
    tar xzf ./actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz && \
    rm ./actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz && \
    ./bin/installdependencies.sh && \
    chown -R runner:runner /home/runner

# 시작 스크립트 설정
COPY --chown=runner:runner start.sh /home/runner/start.sh
RUN chmod +x /home/runner/start.sh

USER runner
ENTRYPOINT ["/home/runner/start.sh"]