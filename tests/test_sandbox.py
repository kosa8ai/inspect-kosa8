"""Tests for the kosa8 Inspect sandbox against the fake kosa8 Cloud API (no network)."""

from __future__ import annotations

import os
from importlib.metadata import entry_points
from pathlib import Path

import pytest
from inspect_ai.util import OutputLimitExceededError, SandboxEnvironmentLimits

import inspect_kosa8._sandbox as mod
from inspect_kosa8 import Kosa8SandboxEnvironment, Kosa8SandboxEnvironmentConfig
from inspect_kosa8._kosa8 import AsyncKosa8Client
from tests.fake_cloud import FakeCloud


@pytest.fixture
def cloud(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeCloud:
    fake = FakeCloud()
    monkeypatch.setattr(mod, "_make_client", lambda cfg: AsyncKosa8Client(api_key=fake.api_key, http_client=fake.async_client(), stream=cfg.stream))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return fake


@pytest.fixture
def config(tmp_path: Path) -> Kosa8SandboxEnvironmentConfig:
    ws = tmp_path / "ws"
    ws.mkdir()
    return Kosa8SandboxEnvironmentConfig(template="python", workspace=str(ws), persist_dirs=())


async def _env(config: Kosa8SandboxEnvironmentConfig) -> Kosa8SandboxEnvironment:
    await Kosa8SandboxEnvironment.task_init("t", config)
    envs = await Kosa8SandboxEnvironment.sample_init("t", config, {})
    assert list(envs) == ["default"]
    return envs["default"].as_type(Kosa8SandboxEnvironment)


def test_entry_point_is_registered() -> None:
    eps = [ep for ep in entry_points(group="inspect_ai") if ep.name == "inspect_kosa8"]
    assert eps and eps[0].value == "inspect_kosa8._registry"


def test_registered_as_kosa8() -> None:
    from inspect_ai.util._sandbox.registry import registry_find_sandboxenv

    assert registry_find_sandboxenv("kosa8") is Kosa8SandboxEnvironment


async def test_lifecycle_creates_and_deletes(cloud: FakeCloud, config: Kosa8SandboxEnvironmentConfig, tmp_path: Path) -> None:
    env = await _env(config)
    assert cloud.requests[0] == ("POST", "/v1/sandboxes", {"template": "python", "ttl_s": 4 * 3600})
    assert (tmp_path / "cache" / "inspect-kosa8" / "sandboxes" / env.sandbox_id).exists()
    await Kosa8SandboxEnvironment.sample_cleanup("t", config, {"default": env}, interrupted=False)
    assert cloud.sandboxes[env.sandbox_id]["state"] == "deleted"
    assert not (tmp_path / "cache" / "inspect-kosa8" / "sandboxes" / env.sandbox_id).exists()


async def test_interrupted_samples_are_deleted_by_task_cleanup(cloud: FakeCloud, config: Kosa8SandboxEnvironmentConfig) -> None:
    env = await _env(config)
    await Kosa8SandboxEnvironment.sample_cleanup("t", config, {"default": env}, interrupted=True)
    assert cloud.sandboxes[env.sandbox_id]["state"] == "running"
    await Kosa8SandboxEnvironment.task_cleanup("t", config, cleanup=True)
    assert cloud.sandboxes[env.sandbox_id]["state"] == "deleted"


async def test_no_cleanup_lists_sandboxes_then_cli_cleanup(cloud: FakeCloud, config: Kosa8SandboxEnvironmentConfig, capsys: pytest.CaptureFixture[str]) -> None:
    env = await _env(config)
    await Kosa8SandboxEnvironment.task_cleanup("t", config, cleanup=False)
    assert env.sandbox_id in capsys.readouterr().out
    assert cloud.sandboxes[env.sandbox_id]["state"] == "running"
    await Kosa8SandboxEnvironment.cli_cleanup(None)
    assert cloud.sandboxes[env.sandbox_id]["state"] == "deleted"


async def test_exec_argv_cwd_env_input(cloud: FakeCloud, config: Kosa8SandboxEnvironmentConfig) -> None:
    env = await _env(config)
    r = await env.exec(["sh", "-c", 'echo "$X"; cat; pwd'], input="piped\n", env={"X": "it's quoted"})
    assert r.success and r.returncode == 0
    lines = r.stdout.splitlines()
    assert lines[0] == "it's quoted"
    assert lines[1] == "piped"
    assert Path(lines[2]).resolve() == Path(config.workspace).resolve()
    os.makedirs(os.path.join(config.workspace, "sub"))
    r = await env.exec(["pwd"], cwd="sub")
    assert Path(r.stdout.strip()).resolve() == Path(config.workspace, "sub").resolve()


async def test_exec_failure_and_missing_command(cloud: FakeCloud, config: Kosa8SandboxEnvironmentConfig) -> None:
    env = await _env(config)
    r = await env.exec(["sh", "-c", "echo bad >&2; exit 3"])
    assert (r.success, r.returncode, r.stderr) == (False, 3, "bad\n")
    r = await env.exec(["definitely-not-a-command-k8"])
    assert r.returncode == 127


async def test_bash_falls_back_to_sh(cloud: FakeCloud, config: Kosa8SandboxEnvironmentConfig, tmp_path: Path) -> None:
    env = await _env(config)
    r = await env.exec(["bash", "--login", "-c", "echo via-shell"])
    assert r.stdout.strip() == "via-shell"
    # Without bash on PATH the same command runs with /bin/sh.
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    for tool in ("sh", "cat", "base64", "rm", "mkdir", "env"):
        src = Path("/bin") / tool if Path("/bin", tool).exists() else Path("/usr/bin") / tool
        (fakebin / tool).symlink_to(src)
    r = await env.exec(["bash", "-c", "command -v bash || echo no-bash"], env={"PATH": str(fakebin)})
    assert r.stdout.strip() == "no-bash"


async def test_exec_timeout_raises(cloud: FakeCloud, config: Kosa8SandboxEnvironmentConfig) -> None:
    if not (Path("/usr/bin/timeout").exists() or Path("/bin/timeout").exists()):
        pytest.skip("no timeout binary on this machine")
    env = await _env(config)
    with pytest.raises(TimeoutError):
        await env.exec(["sleep", "5"], timeout=1)


async def test_host_request_limit_raises_timeout(cloud: FakeCloud, config: Kosa8SandboxEnvironmentConfig) -> None:
    env = await _env(config)
    cloud.fail_exec_with = (502, {"error": "host_failed", "message": "exec failed on the host: The operation was aborted due to timeout"})
    with pytest.raises(TimeoutError):
        await env.exec(["sleep", "100"])


async def test_user_is_unsupported(cloud: FakeCloud, config: Kosa8SandboxEnvironmentConfig) -> None:
    env = await _env(config)
    with pytest.raises(mod.SandboxUserUnsupportedError):
        await env.exec(["true"], user="nobody")
    with pytest.raises(mod.SandboxUserUnsupportedError):
        await env.read_file("x", user="nobody")


async def test_write_read_text_binary_and_relative(cloud: FakeCloud, config: Kosa8SandboxEnvironmentConfig) -> None:
    env = await _env(config)
    await env.write_file("dir/a.txt", "line1\r\nline2\n")
    assert await env.read_file("dir/a.txt") == "line1\r\nline2\n"
    data = os.urandom(200_000)
    await env.write_file(os.path.join(config.workspace, "b.bin"), data)
    assert await env.read_file("b.bin", text=False) == data


async def test_file_errors(cloud: FakeCloud, config: Kosa8SandboxEnvironmentConfig) -> None:
    env = await _env(config)
    with pytest.raises(FileNotFoundError):
        await env.read_file("missing.txt")
    os.makedirs(os.path.join(config.workspace, "d"))
    with pytest.raises(IsADirectoryError):
        await env.read_file("d")
    with pytest.raises(IsADirectoryError):
        await env.write_file("d", "x")
    await env.write_file("f", b"\xff\xfe")
    with pytest.raises(UnicodeDecodeError):
        await env.read_file("f")


async def test_read_limit(cloud: FakeCloud, config: Kosa8SandboxEnvironmentConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    env = await _env(config)
    monkeypatch.setattr(SandboxEnvironmentLimits, "MAX_READ_FILE_SIZE", 10)
    await env.write_file("big", b"x" * 11)
    with pytest.raises(OutputLimitExceededError):
        await env.read_file("big")


def test_config_from_file(tmp_path: Path) -> None:
    f = tmp_path / "kosa8.yaml"
    f.write_text("template: node\nregion: eu-hel1\nttl_s: 600\n")
    cfg = mod._load_config(str(f))
    assert (cfg.template, cfg.region, cfg.ttl_s) == ("node", "eu-hel1", 600)
    with pytest.raises(ValueError, match="does not build"):
        mod._load_config(str(tmp_path / "Dockerfile"))
    assert Kosa8SandboxEnvironment.config_deserialize(cfg.model_dump()) == cfg


async def test_create_retries_when_busy(cloud: FakeCloud, config: Kosa8SandboxEnvironmentConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    real = cloud._create

    def busy_then_ok(body):  # noqa: ANN001, ANN202
        calls["n"] += 1
        if calls["n"] == 1:
            import httpx

            return httpx.Response(503, json={"error": "no_capacity", "message": "no host has room"})
        return real(body)

    monkeypatch.setattr(cloud, "_create", busy_then_ok)

    async def no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", no_sleep)
    env = await _env(config)
    assert calls["n"] == 2 and env.sandbox_id


def test_full_eval_with_mock_model(cloud: FakeCloud, config: Kosa8SandboxEnvironmentConfig, tmp_path: Path) -> None:
    from inspect_ai import Task, eval
    from inspect_ai.dataset import Sample
    from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target, accuracy, scorer
    from inspect_ai.solver import Generate, TaskState, solver
    from inspect_ai.util import sandbox

    @solver
    def run_in_sandbox():  # noqa: ANN202
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            await sandbox().write_file("answer.txt", "42")
            r = await sandbox().exec(["cat", "answer.txt"])
            state.output.completion = r.stdout
            return state

        return solve

    @scorer(metrics=[accuracy()])
    def matches():  # noqa: ANN202
        async def score(state: TaskState, target: Target) -> Score:
            return Score(value=CORRECT if state.output.completion.strip() == target.text else INCORRECT)

        return score

    task = Task(dataset=[Sample(input="x", target="42")], solver=run_in_sandbox(), scorer=matches(), sandbox=("kosa8", config))
    [log] = eval(task, model="mockllm/model", log_dir=str(tmp_path / "logs"), display="none")
    assert log.status == "success", log.error
    assert log.results is not None
    assert log.results.scores[0].metrics["accuracy"].value == 1.0
    assert all(b["state"] == "deleted" for b in cloud.sandboxes.values())
