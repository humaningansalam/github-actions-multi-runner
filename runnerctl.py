#!/usr/bin/env python3
"""A small host-side controller for independent, multi-account GitHub runners.

Requires only Python 3.11+ and Docker Compose v2. Credentials never enter
Compose definitions or the runner environment; registration uses a FIFO.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import platform
import re
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
ID = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
TOKEN_ENV = re.compile(r"^[A-Z][A-Z0-9_]*$")
RUNNER_VERSION = "2.338.0"  # Update with Dockerfile's verified release checksums.


class RunnerError(Exception):
    """A user-facing error (never includes credential material)."""


def native_arch(machine: str | None = None) -> str:
    machine = (machine or platform.machine()).lower()
    result = {
        "x86_64": "x64",
        "amd64": "x64",
        "aarch64": "ARM64",
        "arm64": "ARM64",
    }.get(machine)
    if not result:
        raise RunnerError(
            f"Unsupported host architecture {machine!r}; use native ARM64 or x64 Linux"
        )
    return result


def validate_target(url: str, scope: str) -> tuple[str, str]:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "github.com"
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise RunnerError(
            "target must be a canonical https://github.com/OWNER[/REPO] URL"
        )
    parts = parsed.path.strip("/").split("/")
    if not all(
        re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", p) and p not in (".", "..")
        for p in parts
    ):
        raise RunnerError(f"Invalid target path: {parsed.path!r}")
    if scope == "repo" and len(parts) == 2:
        return "https://github.com/" + "/".join(parts), f"repos/{parts[0]}/{parts[1]}"
    if scope == "org" and len(parts) == 1:
        return "https://github.com/" + parts[0], f"orgs/{parts[0]}"
    raise RunnerError(
        "repo scope requires OWNER/REPO; org scope requires organization OWNER"
    )


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RunnerError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(config, dict):
        raise RunnerError("Config must be a JSON object")
    project = config.get("project", "multi-gh-runners")
    if not isinstance(project, str) or not ID.fullmatch(project):
        raise RunnerError("project must be a lowercase slug beginning with a letter")
    data_root_raw = config.get(
        "data_root", "~/.local/share/github-actions-multi-runner"
    )
    if not isinstance(data_root_raw, str) or not data_root_raw.strip():
        raise RunnerError("data_root must be a nonempty filesystem path")
    data_root = Path(data_root_raw).expanduser()
    if not data_root.is_absolute():
        raise RunnerError("data_root must be an absolute path or start with '~/'")
    data_root = data_root.resolve()
    if str(data_root) in ("/", "/tmp"):
        raise RunnerError(
            "data_root must be a persistent dedicated directory, not / or /tmp"
        )
    runners = config.get("runners")
    if not isinstance(runners, list) or not runners:
        raise RunnerError("runners must be a non-empty array")
    if sys.platform != "linux":
        raise RunnerError(
            "Runner hosts must be Linux; macOS/Windows may control remote hosts but cannot host these images"
        )
    arch = native_arch()
    hostname = (
        re.sub(r"[^a-zA-Z0-9-]", "-", socket.gethostname().split(".")[0]).strip("-")[
            :22
        ]
        or "host"
    )
    checked = []
    names: set[str] = set()
    identities: set[tuple[str, str]] = set()
    for index, raw in enumerate(runners):
        if not isinstance(raw, dict):
            raise RunnerError(f"runners[{index}] must be an object")
        rid = raw.get("id", "")
        if not isinstance(rid, str) or not ID.fullmatch(rid) or rid in names:
            raise RunnerError(f"Invalid or duplicate runner id: {rid!r}")
        names.add(rid)
        scope = raw.get("scope", "repo")
        if scope not in ("repo", "org"):
            raise RunnerError(f"{rid}: scope must be repo or org")
        target, endpoint = validate_target(str(raw.get("target", "")), scope)
        auth_env, auth_file, auth_gh_user = (
            raw.get("auth_env"),
            raw.get("auth_file"),
            raw.get("auth_gh_user"),
        )
        if sum(bool(x) for x in (auth_env, auth_file, auth_gh_user)) != 1:
            raise RunnerError(
                f"{rid}: specify exactly one of auth_env, auth_file, auth_gh_user"
            )
        if auth_env and (
            not isinstance(auth_env, str) or not TOKEN_ENV.fullmatch(auth_env)
        ):
            raise RunnerError(f"{rid}: invalid auth_env")
        if auth_file and not isinstance(auth_file, str):
            raise RunnerError(f"{rid}: auth_file must be a file path")
        if auth_gh_user and (
            not isinstance(auth_gh_user, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", auth_gh_user)
        ):
            raise RunnerError(
                f"{rid}: auth_gh_user must be a GitHub account login name"
            )
        labels = raw.get("labels", [])
        if not isinstance(labels, list) or any(
            not isinstance(x, str) or not LABEL.fullmatch(x) for x in labels
        ):
            raise RunnerError(f"{rid}: labels must be a list of simple strings")
        if len(labels) != len(set(labels)) or any(
            x.lower() in {"self-hosted", "linux", "x64", "arm64", "arm"} for x in labels
        ):
            raise RunnerError(
                f"{rid}: labels must not duplicate default GitHub architecture/OS labels"
            )
        requested_arch = raw.get("host_arch")
        if requested_arch and requested_arch != arch:
            raise RunnerError(
                f"{rid}: configured host_arch {requested_arch!r} does not match native {arch}"
            )
        cpus = raw.get("cpus")
        if cpus is not None and (
            isinstance(cpus, bool)
            or not isinstance(cpus, (int, float))
            or not math.isfinite(cpus)
            or cpus <= 0
        ):
            raise RunnerError(f"{rid}: cpus must be a positive number")
        memory = raw.get("memory")
        if memory is not None and (
            not isinstance(memory, str)
            or not re.fullmatch(r"[1-9][0-9]*[kKmMgG]", memory)
        ):
            raise RunnerError(f"{rid}: memory must look like 4g or 512m")
        docker = raw.get("docker_socket", False)
        if not isinstance(docker, bool):
            raise RunnerError(f"{rid}: docker_socket must be boolean")
        name = raw.get("name", f"{hostname}-{rid}")
        if not isinstance(name, str) or not LABEL.fullmatch(name):
            raise RunnerError(f"{rid}: name must be <=63 simple ASCII characters")
        identity = (target.lower(), name.lower())
        if identity in identities:
            raise RunnerError(f"{rid}: duplicate runner identity {name!r} for {target}")
        identities.add(identity)
        group = raw.get("group")
        if group is not None and (
            scope != "org"
            or not isinstance(group, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,79}", group)
            or group != group.strip()
        ):
            raise RunnerError(
                f"{rid}: group is only supported for org scopes, ASCII names <=80 chars"
            )
        checked.append(
            {
                "id": rid,
                "target": target,
                "endpoint": endpoint,
                "scope": scope,
                "name": name,
                "labels": labels,
                "auth_env": auth_env,
                "auth_file": auth_file,
                "auth_gh_user": auth_gh_user,
                "docker_socket": docker,
                "group": group,
                "arch": arch,
                "cpus": cpus,
                "memory": memory,
            }
        )
    return {
        "project": project,
        "runners": checked,
        "arch": arch,
        "data_root": str(data_root),
    }


def compose_data(
    config: dict[str, Any], socket_path: str = "/var/run/docker.sock"
) -> dict[str, Any]:
    services, volumes = {}, {}
    for r in config["runners"]:
        rid = r["id"]
        volumes[f"state-{rid}"] = {}
        # Match host and container work paths: needed for Docker-based Actions
        # when using the HOST Docker daemon rather than Docker-in-Docker.
        work_dir = work_path(config, rid)
        mounts = [f"state-{rid}:/home/runner", f"{work_dir}:{work_dir}"]
        service: dict[str, Any] = {
            "build": {
                "context": str(ROOT),
                "dockerfile": "Dockerfile",
                "args": {"RUNNER_VERSION": RUNNER_VERSION},
            },
            "image": f"local/multi-gh-runner:{RUNNER_VERSION}",
            "restart": "unless-stopped",
            # Native image on this machine: do not silently run an emulated
            # architecture or mislabel a runner if a remote builder is used.
            "platform": "linux/arm64" if r["arch"] == "ARM64" else "linux/amd64",
            "stop_grace_period": "10m",
            "environment": {
                "RUNNER_NAME": r["name"],
                "RUNNER_TARGET_URL": r["target"],
                "RUNNER_LABELS": ",".join(r["labels"]),
                "RUNNER_GROUP": r["group"] or "",
                "RUNNER_WORK_DIR": work_dir,
            },
            "volumes": mounts,
            "security_opt": ["no-new-privileges:true"],
            "cap_drop": ["ALL"],
            "pids_limit": 2048,
            "logging": {
                "driver": "json-file",
                "options": {"max-size": "10m", "max-file": "5"},
            },
            "healthcheck": {
                "test": ["CMD", "/usr/local/bin/healthcheck.sh"],
                "interval": "30s",
                "timeout": "5s",
                "retries": 3,
                "start_period": "2m",
            },
            "labels": {
                "org.example.runner-id": rid,
                "org.example.github-target": r["target"],
            },
        }
        if r["docker_socket"]:
            try:
                socket_stat = os.stat(socket_path)
                if not stat.S_ISSOCK(socket_stat.st_mode):
                    raise RunnerError(f"{rid}: {socket_path} is not a Unix socket")
                gid = socket_stat.st_gid
            except OSError as exc:
                raise RunnerError(
                    f"{rid}: Docker socket requested but {socket_path} unavailable: {exc}"
                ) from exc
            service["volumes"].append(f"{socket_path}:{socket_path}")
            service["group_add"] = [str(gid)]
        if r.get("cpus") is not None:
            service["cpus"] = r["cpus"]
        if r.get("memory") is not None:
            service["mem_limit"] = r["memory"]
        services[rid] = service
    return {"name": config["project"], "services": services, "volumes": volumes}


def work_path(config: dict[str, Any], rid: str) -> str:
    # Directories are per host and per project. Not part of a Git checkout.
    return str(
        (Path(config["data_root"]) / config["project"] / rid / "_work").resolve()
    )


def write_compose(config: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(compose_data(config), indent=2, ensure_ascii=False) + "\n"
    temp = path.with_suffix(".tmp")
    temp.write_text(content, encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)
    return path


def load_host_token(runner: dict[str, Any]) -> str:
    if runner["auth_env"]:
        token = os.environ.get(runner["auth_env"], "")
        source = runner["auth_env"]
    elif runner.get("auth_gh_user"):
        source = "gh auth token --user " + runner["auth_gh_user"]
        try:
            clean_gh_env = os.environ.copy()
            # GH_TOKEN/GITHUB_TOKEN override gh's stored accounts; never let
            # an unrelated environment token impersonate an explicit account.
            clean_gh_env.pop("GH_TOKEN", None)
            clean_gh_env.pop("GITHUB_TOKEN", None)
            proc = subprocess.run(
                ["gh", "auth", "token", "--user", runner["auth_gh_user"]],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
                env=clean_gh_env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RunnerError(
                f"{runner['id']}: gh CLI authentication unavailable ({type(exc).__name__})"
            ) from exc
        if proc.returncode != 0:
            raise RunnerError(
                f"{runner['id']}: gh CLI account authentication failed for {runner['auth_gh_user']}"
            )
        token = proc.stdout
    else:
        p = Path(runner["auth_file"]).expanduser()
        try:
            perms = stat.S_IMODE(p.stat().st_mode)
            if perms & 0o077:
                raise RunnerError(
                    f"{runner['id']}: token file {p} must have mode 0600 or stricter"
                )
            token = p.read_text(encoding="utf-8")
        except OSError as exc:
            raise RunnerError(
                f"{runner['id']}: cannot read token file {p}: {exc}"
            ) from exc
        source = str(p)
    token = token.strip()
    if not token:
        raise RunnerError(f"{runner['id']}: missing/empty credential from {source}")
    return token


def registration_token(runner: dict[str, Any]) -> str:
    pat = load_host_token(runner)
    url = (
        "https://api.github.com/"
        + runner["endpoint"]
        + "/actions/runners/registration-token"
    )
    request = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "multi-gh-runnerctl/2",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RunnerError(
            f"{runner['id']}: GitHub registration API rejected token (HTTP {exc.code}); check admin permission and scope"
        ) from None
    except (urllib.error.URLError, ValueError) as exc:
        raise RunnerError(
            f"{runner['id']}: registration API unavailable or returned invalid JSON ({type(exc).__name__})"
        ) from None
    token = data.get("token")
    if not isinstance(token, str) or not token:
        raise RunnerError(f"{runner['id']}: registration API returned no token")
    return token


def github_runners(runner: dict[str, Any]) -> list[dict[str, Any]]:
    """Read registered runners via the configured GitHub identity; no auth logs."""
    pat = load_host_token(runner)
    base = "https://api.github.com/" + runner["endpoint"] + "/actions/runners"
    found = []
    for page in range(1, 11):
        request = urllib.request.Request(
            f"{base}?per_page=100&page={page}",
            headers={
                "Authorization": f"Bearer {pat}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "multi-gh-runnerctl/2",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise RunnerError(
                f"{runner['id']}: GitHub runners API returned HTTP {exc.code}"
            ) from None
        except (urllib.error.URLError, ValueError) as exc:
            raise RunnerError(
                f"{runner['id']}: GitHub runners API unavailable ({type(exc).__name__})"
            ) from None
        entries = data.get("runners", [])
        if not isinstance(entries, list):
            raise RunnerError(f"{runner['id']}: unexpected GitHub runners API payload")
        found.extend(x for x in entries if x.get("name") == runner["name"])
        if len(entries) < 100:
            break
    return found


def clean_env(config: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    for runner in config["runners"]:
        if runner.get("auth_env"):
            env.pop(runner["auth_env"], None)
    return env


def compose_cmd(
    compose_path: Path,
    project: str,
    args: list[str],
    *,
    env: dict[str, str],
    input_text: str | None = None,
    capture: bool = False,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = ["docker", "compose", "-p", project, "-f", str(compose_path), *args]
    try:
        return subprocess.run(
            cmd,
            text=True,
            input=input_text,
            capture_output=capture,
            env=env,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RunnerError(
            f"Docker Compose command failed ({type(exc).__name__})"
        ) from exc


def checked(result: subprocess.CompletedProcess[str], action: str) -> None:
    if result.returncode:
        # Never include subprocess output in an exception: other tools may echo secrets.
        raise RunnerError(
            f"{action} failed (exit {result.returncode}); see Docker Compose logs"
        )


def ensure_workspace(config: dict[str, Any], rid: str) -> None:
    """A same-path bind mount for GitHub's nested Docker jobs. No recursive chown."""
    directory = Path(work_path(config, rid))
    directory.mkdir(parents=True, exist_ok=True)
    if directory.stat().st_uid == 10001 and directory.stat().st_gid == 10001:
        return
    # The built runner image is only used to set directory ownership. We never
    # give a running workflow sudo. The host operator already controls Docker.
    cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--user",
        "0",
        "--cap-add",
        "CHOWN",
        "-v",
        f"{directory}:{directory}",
        "--entrypoint",
        "chown",
        f"local/multi-gh-runner:{RUNNER_VERSION}",
        "10001:10001",
        str(directory),
    ]
    try:
        proc = subprocess.run(
            cmd, check=False, capture_output=True, text=True, timeout=40
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RunnerError(
            f"{rid}: workspace permission setup failed ({type(exc).__name__})"
        ) from exc
    if proc.returncode != 0:
        raise RunnerError(
            f"{rid}: workspace ownership setup failed; see host Docker logs"
        )


def expected_profile(config: dict[str, Any], runner: dict[str, Any]) -> str:
    """Must match start.sh's on-disk immutable registration identity."""
    return "|".join(
        [
            runner["target"],
            runner["name"],
            ",".join(runner["labels"]),
            runner["group"] or "",
            work_path(config, runner["id"]),
        ]
    )


def verify_profile(
    config: dict[str, Any], path: Path, runner: dict[str, Any], env: dict[str, str]
) -> None:
    """Reject stale Compose config/Runner registrations without disrupting jobs."""
    response = compose_cmd(
        path,
        config["project"],
        ["exec", "-T", runner["id"], "cat", "/home/runner/.managed-profile"],
        env=env,
        capture=True,
    )
    if response.returncode != 0 or response.stdout.strip() != expected_profile(
        config, runner
    ):
        raise RunnerError(
            f"{runner['id']}: active Runner registration differs from runners.json; "
            "do not use normal up for identity changes; deregister and migrate during a maintenance window"
        )


def wait_for_fifo(
    path: Path, project: str, rid: str, env: dict[str, str], timeout: float = 50
) -> bool:
    """True if a fresh runner needs a registration token, False if configured."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready = compose_cmd(
            path,
            project,
            ["exec", "-T", rid, "test", "-p", "/home/runner/.registration-token"],
            env=env,
            capture=True,
        )
        if ready.returncode == 0:
            return True
        ready = compose_cmd(
            path,
            project,
            ["exec", "-T", rid, "/usr/local/bin/healthcheck.sh"],
            env=env,
            capture=True,
        )
        if ready.returncode == 0:
            return False
        time.sleep(1)
    raise RunnerError(
        f"{rid}: runner did not become ready for registration; check 'logs {rid}'"
    )


def wait_for_healthy(
    path: Path, project: str, rid: str, env: dict[str, str], timeout: float = 90
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready = compose_cmd(
            path,
            project,
            ["exec", "-T", rid, "/usr/local/bin/healthcheck.sh"],
            env=env,
            capture=True,
        )
        if ready.returncode == 0:
            return
        time.sleep(2)
    raise RunnerError(
        f"{rid}: runner listener did not become healthy; check logs and remote registration"
    )


def do_up(
    config: dict[str, Any],
    path: Path,
    selected: list[str],
    *,
    build: bool,
    recreate: bool = False,
) -> None:
    env = clean_env(config)
    # All runner services use the same image on each native-architecture host.
    # Build only once; service registration is still serialized below.
    if build and selected:
        checked(
            compose_cmd(path, config["project"], ["build", selected[0]], env=env),
            "Build runner image",
        )
    for rid in selected:
        r = next(item for item in config["runners"] if item["id"] == rid)
        print(f"Starting {rid} ({r['target']}; {r['arch']})", flush=True)
        if recreate:
            matches = github_runners(r)
            if (
                len(matches) != 1
                or matches[0].get("busy") is not False
                or matches[0].get("status") != "online"
            ):
                raise RunnerError(
                    f"{rid}: refusing to recreate; remote Runner must be uniquely registered, online and idle"
                )
            print(
                f"  {rid}: idle at GitHub API check; avoid new job submissions during this maintenance operation",
                flush=True,
            )
        # The runner image must exist before the one-time workspace UID fix.
        ensure_workspace(config, rid)
        mode = ["--force-recreate"] if recreate else ["--no-recreate"]
        checked(
            compose_cmd(
                path, config["project"], ["up", "-d", "--no-build", *mode, rid], env=env
            ),
            f"Start {rid}",
        )
        if not wait_for_fifo(path, config["project"], rid, env):
            verify_profile(config, path, r, env)
            print(
                f"  {rid}: existing registration retained and identity matches",
                flush=True,
            )
            continue
        token = registration_token(r)
        try:
            result = compose_cmd(
                path,
                config["project"],
                [
                    "exec",
                    "-T",
                    rid,
                    "sh",
                    "-c",
                    "cat > /home/runner/.registration-token",
                ],
                env=env,
                input_text=token + "\n",
                capture=True,
                timeout=30,
            )
            checked(result, f"Send one-time registration token to {rid}")
        finally:
            del token
        print(
            f"  {rid}: registration token delivered; checking Runner.Listener ...",
            flush=True,
        )
        wait_for_healthy(path, config["project"], rid, env)
        verify_profile(config, path, r, env)
        print(
            f"  {rid}: local listener healthy (run 'doctor' to confirm GitHub online)",
            flush=True,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "runners.json",
        help="Path to a local JSON manifest",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "check",
        help="Validate manifest; show native architecture and safe routing labels",
    )
    sub.add_parser(
        "render", help="Generate a Docker Compose JSON file (no credentials)"
    )
    p_up = sub.add_parser(
        "up", help="Build/start runners without recreating running containers"
    )
    p_up.add_argument("services", nargs="*", help="Runner IDs; omitted means all")
    p_up.add_argument(
        "--no-build", action="store_true", help="Use a previously built image"
    )
    p_recreate = sub.add_parser(
        "recreate",
        help="Explicit maintenance: rebuild/recreate only runners confirmed idle",
    )
    p_recreate.add_argument("services", nargs="*", help="Runner IDs; omitted means all")
    p_recreate.add_argument(
        "--no-build", action="store_true", help="Use a previously built image"
    )
    sub.add_parser("status", help="Docker Compose ps")
    p_doctor = sub.add_parser(
        "doctor",
        help="Check registered Runner online/labels on GitHub (requires configured auth)",
    )
    p_doctor.add_argument(
        "--wait",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Wait for GitHub API status to become online after registration",
    )
    p_down = sub.add_parser(
        "down", help="Stop runners only when online/idle; retain state/work volumes"
    )
    p_down.add_argument(
        "--force",
        action="store_true",
        help="Allow stop when status cannot be verified; may interrupt a job",
    )
    p_log = sub.add_parser("logs", help="Follow logs from a runner")
    p_log.add_argument("service", help="Runner ID")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        known = {r["id"] for r in config["runners"]}
        selected = [r["id"] for r in config["runners"]]
        if args.command in ("up", "recreate") and args.services:
            selected = args.services
        if args.command == "logs":
            selected = [args.service]
        unknown = set(selected) - known
        if unknown:
            raise RunnerError(f"Unknown runner(s): {', '.join(sorted(unknown))}")
        if args.command == "check":
            print(f"Project {config['project']}, native Linux {config['arch']}")
            for r in config["runners"]:
                extra = ",".join(r["labels"]) or "(none)"
                print(
                    f"  {r['id']}: {r['scope']} {r['target']} -> {r['name']}; "
                    f"labels=self-hosted,linux,{config['arch']} + {extra}; docker_socket={r['docker_socket']}"
                )
            return 0
        path = write_compose(
            config, ROOT / ".runnerctl" / config["project"] / "compose.json"
        )
        if args.command == "render":
            print(f"Generated {path} (no PATs or registration tokens)")
            return 0
        env = clean_env(config)
        if args.command in ("up", "recreate"):
            lock = ROOT / ".runnerctl" / config["project"] / "operation.lock"
            with lock.open("a+") as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                do_up(
                    config,
                    path,
                    selected,
                    build=not args.no_build,
                    recreate=args.command == "recreate",
                )
        elif args.command == "status":
            checked(compose_cmd(path, config["project"], ["ps"], env=env), "status")
        elif args.command == "doctor":
            if args.wait < 0 or args.wait > 600:
                raise RunnerError("doctor --wait must be between 0 and 600 seconds")
            problems = 0
            for r in config["runners"]:
                expected = {
                    "self-hosted",
                    "linux",
                    r["arch"].lower(),
                    *(x.lower() for x in r["labels"]),
                }
                deadline = time.monotonic() + args.wait
                while True:
                    runners = github_runners(r)
                    ready = False
                    if len(runners) == 1:
                        runner = runners[0]
                        labels = {
                            str(x.get("name", "")).lower()
                            for x in runner.get("labels", [])
                        }
                        ready = (
                            runner.get("status") == "online"
                            and str(runner.get("os", "")).lower() == "linux"
                            and expected <= labels
                        )
                    if ready or time.monotonic() >= deadline:
                        break
                    time.sleep(min(3, max(0, deadline - time.monotonic())))
                if not runners:
                    print(
                        f"MISSING {r['id']}: {r['name']} not registered at {r['target']}"
                    )
                    problems += 1
                elif len(runners) > 1:
                    print(
                        f"AMBIGUOUS {r['id']}: multiple registered runners have this name"
                    )
                    problems += 1
                else:
                    runner = runners[0]
                    labels = {
                        str(x.get("name", "")).lower() for x in runner.get("labels", [])
                    }
                    missing = sorted(expected - labels)
                    if not ready:
                        problems += 1
                    print(
                        f"{r['id']}: status={runner.get('status')} busy={runner.get('busy')} "
                        f"os={runner.get('os')} labels={'ok' if not missing else 'missing ' + ','.join(missing)}"
                    )
            if problems:
                raise RunnerError(
                    f"doctor found {problems} missing/offline/mismatched runner(s)"
                )
        elif args.command == "down":
            if not args.force:
                for r in config["runners"]:
                    entries = github_runners(r)
                    if (
                        len(entries) != 1
                        or entries[0].get("status") != "online"
                        or entries[0].get("busy") is not False
                    ):
                        raise RunnerError(
                            f"{r['id']}: stop refused (must be uniquely online and idle); use --force only during planned downtime"
                        )
                print(
                    "Runners were idle at last GitHub check; prevent new job submissions during shutdown",
                    flush=True,
                )
            else:
                print(
                    "WARNING: --force may interrupt active jobs; keeping registration/work volumes",
                    flush=True,
                )
            checked(compose_cmd(path, config["project"], ["down"], env=env), "down")
        elif args.command == "logs":
            checked(
                compose_cmd(
                    path,
                    config["project"],
                    ["logs", "--tail=100", "-f", args.service],
                    env=env,
                ),
                "logs",
            )
        return 0
    except BlockingIOError:
        print(
            "Error: another runnerctl up/recreate is running on this host",
            file=sys.stderr,
        )
        return 1
    except RunnerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
