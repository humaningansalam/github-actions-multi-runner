# Independent operational review — 2026-10-16

This document distinguishes **observed behavior** from **untested assumptions**. It is a snapshot for PR #1, not a guarantee against regressions.

## Findings corrected before merge

| Issue | Severity | Resolution |
|---|---|---|
| Runner listener healthy while GitHub API still temporarily reports offline | Medium | `doctor --wait SECONDS`; checks labels and OS, not only CPU architecture |
| `up --no-recreate` could silently continue using stale runner settings | High | Check persisted registration identity matches host manifest; require explicit maintenance for changes |
| Explicit `gh` account could be overridden by ambient `GH_TOKEN`/`GITHUB_TOKEN` | Medium | Remove ambient overrides from the account-specific `gh auth token --user` subprocess |
| Docker build performed with a remote builder may select the wrong CPU platform | Medium | Pin Compose platform to `linux/amd64` or `linux/arm64` based on native host |
| JSON `NaN`/`Infinity` CPU limits can pass naive checks | Low | Reject non-finite values |

## Verified

- Python unit tests: **26 passed** at review time; `ruff check`, shell parsing, Docker Compose render/config validation.
- Linux x86-64 Docker image build, GitHub Runner archive SHA256 check, and container bootstrap succeeded.
- Real GitHub API registration of an isolated test Runner with `auth_gh_user`, observed `Listening for Jobs`, GitHub `online`, and clean remote deregistration.
- Real Runner lifecycle: normal `up --no-build` did not recreate the container; `recreate --no-build` retained the registered Runner credentials and returned to healthy status; safe `down` succeeded while idle.
- **Two different GitHub accounts**, two distinct repositories, two independent containers: both registered, returned GitHub `online`, label checks passed, and both were fully unregistered/cleaned after testing.
- Simulated registration/restart with a stub confirmed persistence and no unintended re-registration.

## Not verified here / remaining risks

- No native **ARM64** test host was available. Docker on the x86-64 review host did not have working ARM64 binfmt, so no ARM64 production claim is made. The optional `examples/workflows/validate.yml` requests native GitHub-hosted x64 and ARM64 machines but is not automatically enabled.
- Full workflow jobs with nested Docker-based Actions, Unity/Flutter/Android SDKs, or build artifact download were not executed on the user's distributed production machines. Run the recommended small job before production workloads.
- `docker_socket: true` grants broad access to the **host Docker daemon**. It is not a tenant isolation boundary. Use only for trusted repositories; for untrusted code use dedicated VM/machine or ephemeral isolated Runner infrastructure.
- GitHub API busy/online checks are point-in-time; there is a race before stopping/recreating. Operators must drain jobs or arrange maintenance windows to prevent new assignments.
- Persistent `_work` and host Docker images/caches survive jobs. They need storage monitoring, selective cleanup, and protection from untrusted cross-repository reuse.
- Full automated failover across distributed machines is **not** implemented; GitHub `runs-on` labels route among available registered Runners.
- A real GitHub Actions validation workflow is shipped as a **template only** because workflow-file GitHub OAuth scope was unavailable during upload.

## Production gate

Merge is suitable for a **versioned management tool**, not proof that every ARM build or production job works. On each destination host, first run `check`, `up`, `doctor --wait 60` and one small trusted workflow job; retain the previous deployment until that succeeds.
