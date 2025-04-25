# GitHub Actions Multi-Runner

Docker 기반의 GitHub Actions 셀프 호스팅 러너 관리 도구입니다. 하나의 서버에서 여러 GitHub 저장소를 위한 셀프 호스팅 러너를 쉽게 설정하고 관리할 수 있습니다.

## 기능

- Docker 및 Docker Compose를 활용한 간편한 셀프 호스팅 러너 관리
- 여러 GitHub 저장소에 대한 독립적인 러너 실행
- 환경 변수를 통한 안전한 토큰 관리
- 필요에 따라 쉽게 확장 가능한 구조

## 설치 요구사항

- Docker
- Docker Compose
- Git

## 사용 방법

1. 저장소 클론:
   ```bash
   git clone https://github.com/humaningansalam/github-actions-multi-runner.git
   cd github-actions-multi-runner
   ```

2. `.env` 파일 설정:
   - 각 GitHub 저장소에서 러너 토큰 발급 (Settings > Actions > Runners > New self-hosted runner)
   - `.env` 파일에 토큰 입력
   ```
   REPO1_TOKEN=AAAAAAAAAAAAAAAAAAAAA
   REPO2_TOKEN=BBBBBBBBBBBBBBBBBBBBB
   ```

3. `docker-compose.yml` 파일 수정:
   - `GITHUB_URL` 값을 사용할 각 저장소 URL로 변경
   - 필요에 따라 러너 라벨 및 이름 수정

4. 러너 실행:
   ```bash
   docker-compose up -d
   ```

5. 상태 확인:
   ```bash
   docker-compose ps
   ```

## 구성 파일 설명

- `Dockerfile`: GitHub Actions 러너를 실행하기 위한 Docker 이미지 정의
- `start.sh`: 러너 설정 및 실행 스크립트
- `docker-compose.yml`: 여러 저장소를 위한 러너 서비스 구성
- `.env`: 런타임 시 필요한 토큰과 환경 변수 설정

## 새 저장소 추가 방법

`docker-compose.yml` 파일에 새 서비스를 추가하고 `.env` 파일에 해당 토큰을 추가합니다:

```yaml
runner-repo3:
  build:
    context: .
    dockerfile: Dockerfile
  container_name: github-runner-repo3
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock
    - runner-repo3-data:/home/runner/_work
  environment:
    - GITHUB_URL=https://github.com/username/repo3
    - GITHUB_TOKEN=${REPO3_TOKEN}
    - RUNNER_NAME=runner-repo3
    - RUNNER_LABELS=self-hosted,linux,x64
  restart: unless-stopped
```

## 주의사항

- 토큰은 일회성이므로 러너가 등록된 후에는 재사용할 수 없습니다
- Docker 볼륨을 사용하여 빌드 작업 디렉토리의 데이터 보존
- 워크플로우에서 Docker 명령을 사용하려면 호스트의 Docker 소켓을 마운트해야 합니다

## 라이선스

MIT
