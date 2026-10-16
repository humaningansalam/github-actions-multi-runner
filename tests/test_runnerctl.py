import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import runnerctl


class RunnerCtlTests(unittest.TestCase):
    def config(
        self,
        *,
        scope="repo",
        target="https://github.com/example/demo",
        auth_env="GH_PAT_EXAMPLE",
        **kwargs,
    ):
        row = {
            "id": "alpha",
            "scope": scope,
            "target": target,
            "auth_env": auth_env,
            "labels": ["build", "test"],
            "docker_socket": False,
        }
        row.update(kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runners.json"
            path.write_text(json.dumps({"project": "runners-test", "runners": [row]}))
            with patch("runnerctl.native_arch", return_value="ARM64"):
                return runnerctl.load_config(path)

    def test_host_architecture_detection(self):
        self.assertEqual(runnerctl.native_arch("aarch64"), "ARM64")
        self.assertEqual(runnerctl.native_arch("x86_64"), "x64")
        with self.assertRaises(runnerctl.RunnerError):
            runnerctl.native_arch("ppc64le")

    def test_repo_url_and_org_url(self):
        c = self.config()
        self.assertEqual(c["runners"][0]["endpoint"], "repos/example/demo")
        c = self.config(scope="org", target="https://github.com/example")
        self.assertEqual(c["runners"][0]["endpoint"], "orgs/example")

    def test_url_rejects_path_injection(self):
        for url in (
            "http://github.com/a/b",
            "https://evil.org/a/b",
            "https://github.com/a/b?token=1",
            "https://github.com/a/b/extra",
            "https://github.com/a",
            "https://github.com/a/%2e%2e",
            "https://baduser@github.com/a/b",
            "https://github.com/a/..",
        ):
            with self.subTest(url=url), self.assertRaises(runnerctl.RunnerError):
                self.config(target=url)

    def test_labels_are_not_allowed_to_misrepresent_architecture(self):
        with self.assertRaises(runnerctl.RunnerError):
            self.config(labels=["x64"])

    def test_configured_arch_mismatch_fails(self):
        with self.assertRaises(runnerctl.RunnerError):
            self.config(host_arch="x64")

    def test_multiple_accounts_and_no_credentials_in_compose(self):
        config = self.config()
        other = config["runners"][0].copy()
        other.update(
            {
                "id": "beta",
                "target": "https://github.com/other/repo",
                "auth_env": "GH_PAT_OTHER",
                "name": "host-beta",
                "docker_socket": False,
            }
        )
        config["runners"].append(other)
        model = runnerctl.compose_data(config)
        self.assertEqual(set(model["services"]), {"alpha", "beta"})
        self.assertEqual(len(model["volumes"]), 2)
        content = json.dumps(model)
        for secret in ("GH_PAT_EXAMPLE", "GH_PAT_OTHER", "secret", "Bearer"):
            self.assertNotIn(secret, content)
        self.assertEqual(
            model["services"]["alpha"]["environment"]["RUNNER_TARGET_URL"],
            "https://github.com/example/demo",
        )
        self.assertNotIn("/var/run/docker.sock:/var/run/docker.sock", content)
        work = runnerctl.work_path(config, "alpha")
        self.assertIn(f"{work}:{work}", model["services"]["alpha"]["volumes"])
        self.assertEqual(
            model["services"]["alpha"]["environment"]["RUNNER_WORK_DIR"], work
        )

    def test_opt_in_docker_socket_uses_host_group(self):
        config = self.config()
        config["runners"][0]["docker_socket"] = True
        with patch("runnerctl.os.stat") as stat_mock:
            stat_mock.return_value.st_gid = 1500
            stat_mock.return_value.st_mode = 0o140660  # Unix domain socket
            model = runnerctl.compose_data(config)
        service = model["services"]["alpha"]
        self.assertEqual(service["group_add"], ["1500"])
        self.assertIn("/var/run/docker.sock:/var/run/docker.sock", service["volumes"])

    def test_gh_auth_named_account_without_docker_secret(self):
        config = self.config(auth_env=None, auth_gh_user="account-b")
        runner = config["runners"][0]
        with patch(
            "runnerctl.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="gh-account-token\n"),
        ) as gh:
            self.assertEqual(runnerctl.load_host_token(runner), "gh-account-token")
        self.assertEqual(
            gh.call_args.args[0], ["gh", "auth", "token", "--user", "account-b"]
        )
        generated = json.dumps(runnerctl.compose_data(config))
        self.assertNotIn("gh-account-token", generated)
        self.assertNotIn("account-b", generated)

    def test_gh_auth_unavailable_fails_safely(self):
        runner = self.config(auth_env=None, auth_gh_user="account-b")["runners"][0]
        with patch(
            "runnerctl.subprocess.run",
            return_value=MagicMock(returncode=1, stdout="", stderr="sensitive"),
        ):
            with self.assertRaises(runnerctl.RunnerError) as error:
                runnerctl.load_host_token(runner)
        self.assertNotIn("sensitive", str(error.exception))

    def test_token_file_permission_check(self):
        runner = self.config()["runners"][0]
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "private.pat"
            file.write_text("secret-token\n")
            runner["auth_env"] = None
            runner["auth_file"] = str(file)
            file.chmod(0o644)
            with self.assertRaises(runnerctl.RunnerError):
                runnerctl.load_host_token(runner)
            file.chmod(0o600)
            self.assertEqual(runnerctl.load_host_token(runner), "secret-token")

    def test_registration_api_requests_short_lived_token(self):
        config = self.config()
        runner = config["runners"][0]
        response = MagicMock()
        response.read.return_value = b'{"token":"short-lived-registration-token"}'
        response.__enter__.return_value = response
        with (
            patch.dict(os.environ, {"GH_PAT_EXAMPLE": "dummy-long-lived-secret"}),
            patch("runnerctl.urllib.request.urlopen", return_value=response) as api,
        ):
            value = runnerctl.registration_token(runner)
        self.assertEqual(value, "short-lived-registration-token")
        req = api.call_args.args[0]
        self.assertEqual(
            req.full_url,
            "https://api.github.com/repos/example/demo/actions/runners/registration-token",
        )
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.headers["Authorization"], "Bearer dummy-long-lived-secret")

    def test_environment_redacts_account_credentials_from_docker(self):
        config = self.config()
        with patch.dict(os.environ, {"GH_PAT_EXAMPLE": "secret", "HOME": "/tmp/home"}):
            environment = runnerctl.clean_env(config)
        self.assertNotIn("GH_PAT_EXAMPLE", environment)
        self.assertEqual(environment["HOME"], "/tmp/home")

    def test_fifo_handshake_only_injects_one_time_token(self):
        config = self.config()
        compose = Path("/fake/compose.json")
        calls = []

        def fake_compose(_path, _project, args, **kw):
            calls.append((args, kw.get("input_text")))
            return MagicMock(returncode=0)

        with (
            patch("runnerctl.compose_cmd", side_effect=fake_compose),
            patch("runnerctl.ensure_workspace"),
            patch("runnerctl.registration_token", return_value="ephemeral-token"),
            patch("runnerctl.wait_for_healthy"),
        ):
            runnerctl.do_up(config, compose, ["alpha"], build=False)
        self.assertTrue(
            any(
                "cat > /home/runner/.registration-token" in " ".join(cmd)
                and value == "ephemeral-token\n"
                for cmd, value in calls
            )
        )
        self.assertFalse(any("ephemeral-token" in " ".join(cmd) for cmd, _ in calls))

    def test_compose_render_is_valid_json(self):
        config = self.config()
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "compose.json"
            runnerctl.write_compose(config, file)
            self.assertEqual(
                json.loads(file.read_text()), runnerctl.compose_data(config)
            )
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)

    def test_persistent_data_root_can_be_customized(self):
        cfg = self.config()
        self.assertIn(
            ".local/share/github-actions-multi-runner",
            runnerctl.work_path(cfg, "alpha"),
        )
        cfg["data_root"] = "/srv/build-runner-storage"
        self.assertEqual(
            runnerctl.work_path(cfg, "alpha"),
            "/srv/build-runner-storage/runners-test/alpha/_work",
        )

    def test_invalid_data_root(self):
        for root in ("", "/", "/tmp", "../relative"):
            with self.subTest(root=root), self.assertRaises(runnerctl.RunnerError):
                self.config_from_manifest({"data_root": root})

    def config_from_manifest(self, overrides):
        doc = {
            "project": "runner-sandbox",
            "runners": [
                {
                    "id": "test",
                    "target": "https://github.com/org/repo",
                    "auth_env": "SAMPLE_TOKEN",
                }
            ],
        }
        doc.update(overrides)
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "runners.json"
            src.write_text(json.dumps(doc))
            return runnerctl.load_config(src)

    def test_no_recreate_on_normal_up(self):
        cfg = self.config()
        calls = []

        def fake_compose(_p, _project, args, **kw):
            calls.append(args)
            return MagicMock(returncode=0)

        with (
            patch("runnerctl.compose_cmd", side_effect=fake_compose),
            patch("runnerctl.ensure_workspace"),
            patch("runnerctl.wait_for_fifo", return_value=False),
        ):
            runnerctl.do_up(cfg, Path("fake.json"), ["alpha"], build=False)
        self.assertIn("--no-recreate", calls[0])
        self.assertNotIn("--force-recreate", calls[0])

    def test_refuses_recreate_when_busy(self):
        cfg = self.config()
        with (
            patch(
                "runnerctl.compose_cmd", return_value=MagicMock(returncode=0)
            ) as comp,
            patch(
                "runnerctl.github_runners",
                return_value=[{"name": "a", "status": "online", "busy": True}],
            ),
            patch("runnerctl.ensure_workspace"),
        ):
            with self.assertRaisesRegex(runnerctl.RunnerError, "refusing to recreate"):
                runnerctl.do_up(
                    cfg, Path("fake.json"), ["alpha"], build=False, recreate=True
                )
        comp.assert_not_called()

    def test_wait_for_fifo_requires_listener_not_only_state_file(self):
        outputs = [
            MagicMock(returncode=1),
            MagicMock(returncode=1),
            MagicMock(returncode=1),
            MagicMock(returncode=0),
        ]
        with (
            patch("runnerctl.compose_cmd", side_effect=outputs),
            patch("runnerctl.time.sleep"),
        ):
            self.assertFalse(
                runnerctl.wait_for_fifo(
                    Path("fake.json"), "test", "alpha", {}, timeout=10
                )
            )

    def test_doctor_api_pagination_and_response(self):
        cfg = self.config()
        runner = cfg["runners"][0]
        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "runners": [
                    {"name": "other", "status": "online"},
                    {
                        "name": runner["name"],
                        "status": "online",
                        "busy": False,
                        "labels": [],
                    },
                ],
                "total_count": 2,
            }
        ).encode()
        response.__enter__.return_value = response
        with (
            patch.dict(os.environ, {"GH_PAT_EXAMPLE": "dummy"}),
            patch("runnerctl.urllib.request.urlopen", return_value=response),
        ):
            matches = runnerctl.github_runners(runner)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["name"], runner["name"])

    def test_duplicate_identity_is_rejected(self):
        cfg = self.config()
        base = cfg["runners"][0]
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "dup.json"
            sample = {
                "runners": [
                    {
                        "id": "first",
                        "target": base["target"],
                        "name": "shared",
                        "auth_env": "TOKEN_A",
                    },
                    {
                        "id": "second",
                        "target": base["target"],
                        "name": "shared",
                        "auth_env": "TOKEN_B",
                    },
                ]
            }
            source.write_text(json.dumps(sample))
            with self.assertRaisesRegex(
                runnerctl.RunnerError, "duplicate runner identity"
            ):
                runnerctl.load_config(source)

    def test_no_accidental_namespace_reuse(self):
        config = self.config()
        self.assertIn("runners-test", runnerctl.work_path(config, "alpha"))
        self.assertTrue(runnerctl.work_path(config, "alpha").endswith("/alpha/_work"))


if __name__ == "__main__":
    unittest.main()
