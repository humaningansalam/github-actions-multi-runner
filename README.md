# GitHub Actions Multi-Runner

**여러 GitHub 개인 계정·조직·저장소의 Self-hosted Runner를 여러 Linux 빌드 노드에서 독립적으로 운영하는 소형 관리 도구.** 중앙 서버/Jenkins/Kubernetes 없이, **각 호스트에서 동일한 리포지토리 + 각자의 `runners.json`**으로 구동합니다. x86-64와 ARM64에서 동작합니다.

> ⚠️ **신뢰할 수 있는 저장소의 워크플로만 실행하세요.** Self-hosted Runner는 공격자에게 호스트 자원을 노출할 수 있습니다. Docker 컨테이너는 악의적인 워크플로에 대한 강한 보안 경계가 아닙니다. 특히 `docker_socket: true`는 해당 Runner에 **호스트 Docker의 관리자 수준 접근**을 허용합니다. 공개 저장소의 외부 PR, 포크에서 보내는 미검증 코드를 이 Runner로 실행하지 마세요. 계정 간 보안 격리가 필요하다면 물리/가상 머신을 분리하세요.

## v2 주요 변경

- **멀티 계정:** Runner별 GitHub 소유자/저장소·조직 URL 및 다른 자격 증명 지정. 파일/PAT 환경변수 또는 `gh auth token --user <account>`로 사전 로그인한 여러 GitHub CLI 계정 사용 가능. 저장소마다 Runner 또는 조직 단위 Runner 사용 가능.
- **네이티브 아키텍처:** Linux `x86_64` → `X64`, `aarch64` → `ARM64` Runner 자동 선택. Actions 기본 OS/아키텍처 라벨을 사용하며 사용자 정의 라벨로 `build`, `unity` 등을 추가.
- **안전한 자동 등록:** 호스트의 `runnerctl.py`가 권한 제한된 PAT으로 GitHub 등록 API를 호출하고, 짧게 유효한 일회성 등록 토큰만 FIFO를 통해 전달. **장기 PAT을 컨테이너 환경변수/Compose 이미지/워크플로에 전달하지 않음.**
- **재시작 내구성:** Runner 등록 파일·자동 업데이트는 Docker named volume, 작업 디렉터리는 별도의 영구 host bind mount로 유지. 컨테이너 재생성 시 중복 등록하지 않음.
- **Docker 기반 Actions 지원:** `docker_socket: true`인 경우에도 호스트 Docker 데몬이 볼 수 있도록 Runner 컨테이너와 호스트의 **workspace 절대 경로를 일치**시킴.
- **운영:** 계정별 상태 확인, GitHub 원격 등록 확인(`doctor`), Docker 로그 로테이션, healthcheck, 명시적 opt-in Docker 소켓. 정상 `up`에서는 기존 컨테이너 재생성 금지, 유지보수 시 명시적인 `recreate`만 허용.
- **공급망:** Runner 배포 아카이브 버전·SHA256 고정. `.dockerignore`로 PAT, 워크스페이스 및 로컬 설정이 Docker 빌드 컨텍스트에 들어가지 않도록 제한. 버전을 올릴 때 Dockerfile의 두 SHA256도 함께 갱신.

## 빠른 시작 (각 빌드 머신에서 반복)

요구 사항: **Linux ARM64 또는 x86-64**, Docker Engine/Compose v2, Python **3.11+**, 인터넷 접근(`github.com`, `api.github.com`, GitHub Actions 서비스 도메인). Runner에 별도 `gh` CLI나 Jenkins는 필요 없습니다. Mac/Windows는 이 이미지의 실행 호스트로 지원하지 않습니다.

```bash
git clone https://github.com/humaningansalam/github-actions-multi-runner.git
cd github-actions-multi-runner
cp examples/runners.example.json runners.json
```

`runners.json`을 실제 계정/저장소로 수정합니다. `scope: "repo"`는 개인 계정/저장소, `scope: "org"`는 조직 전체에 등록합니다. 같은 조직 내 여러 저장소라면 가능할 때 **조직 Runner 한 개로 공유**하세요.

```json
{
  "project": "my-runners",
  "data_root": "~/.local/share/github-actions-multi-runner",
  "runners": [
    {
      "id": "personal-web",
      "scope": "repo",
      "target": "https://github.com/my-personal-user/my-app",
      "auth_file": "~/.config/multi-gh-runners/personal.pat",
      "labels": ["build", "web"],
      "docker_socket": false,
      "cpus": 2,
      "memory": "4g"
    },
    {
      "id": "org-docker",
      "scope": "org",
      "target": "https://github.com/my-github-org",
      "auth_env": "GITHUB_PAT_ORG",
      "labels": ["build", "docker"],
      "docker_socket": true
    }
  ]
}
```

**자격 증명:** `auth_file`, `auth_env`, `auth_gh_user` 중 **하나만** 지정. 파일은 `chmod 600`인 호스트 사용자 소유 파일로 두세요. 토큰 자체를 `runners.json`이나 커밋에 넣지 마세요. `.gitignore`는 `runners.json`과 로컬 인증·생성 파일을 제외합니다. `auth_env`는 실행하는 호스트 셸의 환경변수로 전달하며, `runnerctl.py`가 하위 Docker 명령에는 제거합니다.

**이미 `gh` CLI에 개인 계정 여러 개를 로그인해 두었다면** PAT 파일 대신 `"auth_gh_user": "account-a"`를 쓸 수도 있습니다. 이 방식은 빌드 머신에 `gh` CLI가 필요하며 `gh auth token --user account-a`가 정상 동작해야 합니다. 해당 계정 토큰의 GitHub API 권한이 부족하면 등록이 실패하므로, 자동화 서버에서는 필요한 권한만 담은 전용 PAT 파일을 권장합니다.

```bash
mkdir -p ~/.config/multi-gh-runners
# 원하는 비밀 관리자로 PAT를 위 경로에 안전하게 저장한 후:
chmod 600 ~/.config/multi-gh-runners/personal.pat

python3 runnerctl.py check     # 대상, 아키텍처, 라벨/권한 설정 검증
python3 runnerctl.py render    # .runnerctl/multi-gh-runners/compose.json 생성 (PAT 없음)
python3 runnerctl.py up        # 이미지 빌드 → 권한 준비 → 자동 등록 → 시작 (실행 중인 컨테이너 재생성 금지)
python3 runnerctl.py status    # Docker 컨테이너 상태
python3 runnerctl.py doctor    # GitHub에 등록된 Runner online/arch 확인 (PAT 필요)
python3 runnerctl.py logs personal-web
```

`up`은 처음에 이미지를 다운로드/빌드하므로 시간이 걸릴 수 있습니다. 이미 실행 중인 Runner 컨테이너는 `up`을 반복해도 재생성되지 않습니다. 원격 등록 토큰은 **1시간 유효**하며, 실행 시에만 API로 새로 받습니다. 정상적으로 등록된 이후 PAT가 만료되더라도 **기존 Runner 서비스는 저장된 등록 자격으로 계속 실행**할 수 있지만, `doctor`나 재등록에는 PAT가 필요합니다.

`python3 runnerctl.py up`은 반복 실행해도 **기존 컨테이너를 강제로 재생성하지 않습니다**. 변경된 Docker 이미지·환경변수를 적용할 때만 **빌드 제출을 멈추고** `python3 runnerctl.py recreate [runner-id]`를 사용하세요. GitHub API에서 대상 Runner가 **online + idle + 중복 없는 이름**으로 확인돼야 진행합니다. GitHub의 확인 직후 새 작업이 배정될 가능성은 여전히 있으므로 운영 중 실제 작업을 막는 점검 시간을 확보하세요.

`python3 runnerctl.py down`은 **GitHub 측에서 모든 대상 Runner가 online + idle인 경우에만** 중지합니다. 네트워크 장애 등으로 확인할 수 없는 경우에는 중단하지 않습니다. 점검 시간에 작업 중단 위험을 수락할 때만 `python3 runnerctl.py down --force`를 사용하세요. 확인 직후 작업이 새로 배정될 수 있으므로 안전한 중단을 보장하려면 **새 빌드 제출을 먼저 차단**해야 합니다. 등록 상태 volume 및 `_work` 경로는 삭제하지 않습니다. `docker compose down -v`를 수행하면 등록 볼륨을 잃을 수 있으니 주의하세요.

### 아키텍처 및 라벨

호스트의 실제 CPU가 ARM64면 `self-hosted`, `Linux`, `ARM64`, x86-64면 `self-hosted`, `Linux`, `X64` 라벨이 GitHub에 자동 등록됩니다. **`labels`에 `x64`나 `ARM64`를 억지로 선언하지 않습니다.** 필요하면 `host_arch: "ARM64"` 또는 `"x64"`를 넣어 서버별 설정 착오를 검출할 수 있습니다.

```yaml
jobs:
  test_arm:
    runs-on: [self-hosted, Linux, ARM64, build]
    steps:
      - uses: actions/checkout@v4
      - run: uname -m
  test_x64:
    runs-on: [self-hosted, Linux, X64, build]
    steps:
      - uses: actions/checkout@v4
      - run: uname -m
```

각 머신의 설정을 다르게 배치하세요. 본 저장소는 **서버 사이 빌드 자동 분배 시스템이 아니며**, GitHub Actions가 job의 `runs-on` 조건으로 알맞은 등록 Runner를 선택합니다. Unity Editor Linux 빌드에는 x86-64가, Xcode 최종 빌드에는 macOS가 필요하며, 이 컨테이너 하나로 모든 플랫폼 빌드가 가능해지는 것은 아닙니다.

### Runner의 수와 자원 할당

한 Runner 프로세스는 한 번에 한 작업을 받습니다. 같은 서버에서 여러 계정·저장소에 Runner를 열면 **동시에 빌드해 CPU/RAM/디스크를 경쟁할 수 있습니다.** 각 Runner에 `cpus`, `memory`를 지정해 노드 자원을 초과하지 않도록 계획하고, 워크플로에 `concurrency` 제어를 고려하세요. **중요: 이 자원 제한은 Runner 컨테이너에만 적용됩니다. 호스트 Docker 소켓으로 만든 빌드 컨테이너에는 동일한 CPU/RAM 제한이 자동 적용되지 않습니다.** 따라서 계정이 많으면 호스트 Docker 작업 자체를 별도로 통제해야 합니다. 단, GitHub 워크플로의 concurrency 그룹은 저장소 단위이므로 **서로 다른 저장소를 아우르는 글로벌 슬롯 제한을 제공하지 않습니다.** 필요하면 각 서버에서 Runner 수를 줄이거나 별도 스케줄러가 필요합니다.

### Docker 빌드/컨테이너 작업

- Docker가 필요한 Runner에서만 `"docker_socket": true`를 켭니다. 호스트 소켓의 GID를 자동 반영하며 `docker build`·`docker compose` 및 Docker 기반 Actions의 경로 요구를 고려합니다.
- Docker가 필요 없는 Runner는 `false`로 두는 편이 안전합니다.
- `docker_socket: true` Runner는 **다른 Docker 기반 Runner와 같은 호스트 Docker 데몬/이미지/볼륨 영역을 공유**합니다. 서로 불신하는 GitHub 계정을 한 머신에 넣지 마세요.
- Docker 이미지/BuildKit 캐시가 남는 건 의도된 동작입니다. 전역 `docker system prune --volumes`는 다른 Runner의 데이터도 지울 수 있으므로 금지. 사용량부터 `docker system df`, `docker buildx du`로 확인한 뒤 보관 정책에 맞춰 정리합니다.
- 작업 디렉터리: `<data_root>/<project>/<id>/_work` (기본 `~/.local/share/github-actions-multi-runner/...`). **실행 파일 checkout과 별도 경로**로 설정 가능하며 호스트와 컨테이너 양쪽에서 동일한 절대 경로로 bind mount됩니다. **`data_root`는 반드시 재부팅 뒤에도 유지되는 영속 디스크**로 지정하세요. 저장 파일은 루트 이미지가 아니라 Runner 사용자의 **UID/GID 10001**로 소유됩니다. 빌드 결과물은 장기간 `_work`에만 두지 말고 GitHub Artifacts, 릴리즈 또는 별도 저장소로 게시하세요.

### 재시작, 업데이트, 복구

| 상황 | 권장 처리 |
|---|---|
| Docker 재시작 / 시스템 재부팅 | `restart: unless-stopped`로 재실행, 등록 정보와 작업 경로 유지. `data_root`는 `/tmp` 같은 휘발 경로 대신 영구 디스크 사용 |
| `docker compose up --force-recreate` 등 컨테이너 재생성 | 같은 설정·볼륨이 남으면 재등록 없이 복귀 |
| GitHub Runner 소프트웨어 업데이트 | 실행 중 Runner의 자동 업데이트 유지. Docker 이미지 버전과 SHA도 주기적으로 올려 테스트 |
| 등록 정보가 GitHub에서 삭제되거나 손상됨 | `doctor`로 등록 여부 확인. **서버측 Runner 등록 제거 → 로컬 state volume 안전하게 초기화 → `up` 재등록**의 수동 복구 절차 이용. 기존 `_work`는 유지 |
| 계정/저장소/Runner 이름/그룹/라벨 변경 | 기존 Runner는 GitHub에서 등록 해제한 뒤 **state volume**을 초기화하고 다시 등록. 기존 설정과 저장된 상태가 다르면 시작을 거부함 |
| 서버 이름이 같은 머신 2대 | `name`을 각각 고유하게 지정. Runner 이름 충돌 시 다른 서버의 등록을 덮어쓰지 않도록 `--replace`를 기본 사용하지 않음 |
| 상태는 남았지만 이미지 빌드 옵션이 바뀜 | `runnerctl.py up`은 실행 컨테이너를 유지. 점검 창에 `runnerctl.py recreate <id>` 실행 (GitHub online/idle 확인 필요) |

**복구 시 주의:** `docker compose -f .runnerctl/multi-gh-runners/compose.json down -v`처럼 전체 볼륨을 무작정 삭제하지 마세요. GitHub Settings에서 해당 Runner를 정상 등록 해제하고 빌드 중 작업이 없는지 확인한 뒤, **대상 Runner의 state volume만** 제거하세요. Docker Compose project 이름과 볼륨 이름은 `python3 runnerctl.py render` 후 `docker volume ls`로 확인할 수 있습니다. `_work`는 별도 bind mount이므로 state volume 정리로 지워지지 않습니다. GitHub Runner 인증 상태가 사라지면 자동 재등록을 시도합니다.

### 운영 중 점검

```bash
python3 runnerctl.py status
python3 runnerctl.py doctor
python3 runnerctl.py logs personal-web

docker system df
docker buildx du
```

`doctor`는 PAT으로 GitHub 등록된 Runner의 상태, busy, OS/아키텍처 라벨을 확인합니다. Docker가 running이어도 GitHub에서 offline일 수 있으니 둘을 함께 보세요. Runner 컨테이너 로그는 크기·개수 제한으로 로테이션합니다. Runner 내부 `_diag`는 재시작 때 무조건 지우지 않습니다.

### 기존 v1 설치에서 이전하기

1. 기존 Runner의 진행 중 빌드가 끝났는지 확인하고 멈춥니다. 기존 Docker named volume과 등록 상태를 백업합니다.
2. 기존 v1은 `_work`만 볼륨으로 유지하고 `.runner`/`.credentials`는 **컨테이너 레이어에 저장**했습니다. 따라서 새 버전에서 기존 등록을 안전하게 자동 이전한다고 가정하지 마세요.
3. GitHub 저장소/조직 `Settings → Actions → Runners`에서 **구 Runner를 등록 해제**합니다. 기존 저장소별 등록 토큰은 만료될 수 있으므로 새 PAT을 호스트에 설정합니다.
4. `runners.json`을 작성하고 새 `runnerctl.py up`으로 등록합니다. 필요하다면 기존 `_work`의 캐시/산출물만 선별적으로 새 작업 디렉터리로 복원합니다. **서버가 다른 사용자의 저장소를 실행할 수 있는 계정과 함께 공유될 때는 캐시 재사용을 권하지 않습니다.**

### 왜 Jenkins나 ARC가 아닌가?

이 도구의 범위는 여러 **고정 Linux 머신에서 소수의 Runner를 유지**하는 것입니다. GitHub는 대규모 자동 확장 시 일회성(`--ephemeral`) Runner를 추천하며 Kubernetes에서는 Actions Runner Controller(ARC)가 일반적인 선택입니다. 여기서는 비용·운영 부담과 기존 고정 머신에 맞춰 **persistent Runner**를 의도적으로 선택했습니다. 작업마다 완전한 격리가 필요한 경우에는 별도 VM 또는 일회성 Runner 수명주기로 옮겨야 합니다.

### 권한 최소화 메모

저장소 범위 fine-grained PAT에는 해당 저장소의 **Administration (write)** 권한이 필요합니다. 조직 범위는 조직의 **Self-hosted runners (write)** 권한이 필요합니다. 조직 Runner의 접근 가능한 저장소는 GitHub 조직 설정의 Runner group/정책에서 별도로 제한해야 합니다. PAT은 등록/조회에만 쓰고 Actions 워크플로에 전달하지 않습니다.

## 테스트

공개 저장소의 GitHub-hosted **x86-64 + ARM64 네이티브 Docker 빌드**와 Python 테스트를 위한 워크플로 예제를 `examples/workflows/validate.yml`에 제공합니다. CI를 자동 실행하려면 `mkdir -p .github/workflows && cp examples/workflows/validate.yml .github/workflows/validate.yml`로 활성화할 수 있습니다. GitHub 워크플로 파일을 푸시하려면 **GitHub 인증에 workflow 수정 권한**이 필요합니다. 현재 CI 템플릿은 예제로 제공하며, ARM64 실제 Docker 빌드 검증이 완료됐다고 주장하지 않습니다.

```bash
python3 -m unittest discover -s tests -v
bash -n start.sh scripts/healthcheck.sh
python3 runnerctl.py --config examples/runners.example.json check
python3 runnerctl.py --config examples/runners.example.json render
docker compose -f .runnerctl/multi-gh-runners/compose.json config --quiet
```

공식 참고 자료: [GitHub Self-hosted Runners](https://docs.github.com/en/actions/concepts/runners/self-hosted-runners) · [Runner REST API](https://docs.github.com/en/rest/actions/self-hosted-runners) · [Runner Security](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions)

MIT License.
