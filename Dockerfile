# Linux x64/ARM64 self-hosted runner. Native architecture is selected by BuildKit.
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG TARGETARCH
ARG RUNNER_VERSION=2.338.0
ENV RUNNER_IMAGE_VERSION=${RUNNER_VERSION}
ENV TZ=Etc/UTC

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash ca-certificates curl git git-lfs jq gnupg procps tar tini unzip \
    && install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" \
       > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli docker-buildx-plugin docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

# Hashes verified against the official actions/runner release v2.338.0.
# When updating RUNNER_VERSION, update both hashes too; fail closed otherwise.
ARG RUNNER_SHA256_X64=af4b794c1bc41d73d40535e3fe092a39f9679cd8d965954c2aca25a05ca41d32
ARG RUNNER_SHA256_ARM64=628b4a7258487b80c1d3c221095a7ded349f5ae0175fd3dfab708d07428f041b
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) runner_arch=x64; runner_sha="${RUNNER_SHA256_X64}" ;; \
      arm64) runner_arch=arm64; runner_sha="${RUNNER_SHA256_ARM64}" ;; \
      *) echo "Unsupported target architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    install -d /opt/runner-dist; \
    curl --fail --location --silent --show-error --retry 3 \
      "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${runner_arch}-${RUNNER_VERSION}.tar.gz" \
      --output /tmp/runner.tar.gz; \
    echo "${runner_sha}  /tmp/runner.tar.gz" | sha256sum --check; \
    tar -xzf /tmp/runner.tar.gz -C /opt/runner-dist; \
    rm /tmp/runner.tar.gz; \
    /opt/runner-dist/bin/installdependencies.sh; \
    chmod -R a+rX /opt/runner-dist

RUN useradd --uid 10001 --create-home --shell /bin/bash runner \
    && install -d -o runner -g runner /home/runner/_work
COPY --chmod=0755 start.sh /usr/local/bin/start.sh
COPY --chmod=0755 scripts/healthcheck.sh /usr/local/bin/healthcheck.sh
USER runner
WORKDIR /home/runner
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/start.sh"]
