"""Inspect's portable sandbox checks (``inspect_ai.util._sandbox.self_check``)
against the fake kosa8 Cloud API, with the files API on and off.

The fake runs each command as a local subprocess, as the user running the
tests, so the checks that need root or other accounts do not apply here; the
live suite (``tests/test_live.py``) runs the same checks on kosa8 Cloud.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from inspect_ai.util._sandbox.self_check import *

import inspect_kosa8._sandbox as mod
from inspect_kosa8 import Kosa8SandboxEnvironment, Kosa8SandboxEnvironmentConfig
from inspect_kosa8._kosa8 import AsyncKosa8Client
from tests.fake_cloud import FakeCloud

# Checks that need what a local, non-root subprocess cannot give.
_LOCAL_XFAIL = {
    # Commands run as the image's default user, and here that is not root, so
    # the checks cannot add the account they switch to.
    "test_exec_as_user": "needs root to add an account",
    "test_read_file_as_user": "needs root to add an account",
    "test_write_file_as_user": "needs root to add an account",
}
if sys.platform == "darwin":
    # 1 MiB of arguments to an external printf is over macOS's ARG_MAX (Linux allows 2 MiB).
    _LOCAL_XFAIL["test_exec_large_command"] = "macOS ARG_MAX is 1 MiB"


@pytest.fixture(params=[True, False], ids=["files-api", "exec-files"])
async def sandbox_env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[Kosa8SandboxEnvironment]:
    reason = _LOCAL_XFAIL.get(request.node.originalname)
    if reason:
        request.node.add_marker(pytest.mark.xfail(reason=reason, strict=True))
    ws = tmp_path / "ws"
    ws.mkdir()
    fake = FakeCloud(files_api=request.param, files_root=str(ws), host_timeout_s=30)
    monkeypatch.setattr(
        mod,
        "_make_client",
        lambda cfg: AsyncKosa8Client(api_key=fake.api_key, http_client=fake.async_client(), files_root=str(ws)),
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    cfg = Kosa8SandboxEnvironmentConfig(workspace=str(ws), persist_dirs=())
    await Kosa8SandboxEnvironment.task_init("self_check", cfg)
    envs = await Kosa8SandboxEnvironment.sample_init("self_check", cfg, {})
    env = envs["default"].as_type(Kosa8SandboxEnvironment)
    try:
        yield env
    finally:
        await Kosa8SandboxEnvironment.sample_cleanup("self_check", cfg, envs, interrupted=False)
