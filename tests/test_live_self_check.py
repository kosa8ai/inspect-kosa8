"""Inspect's portable sandbox checks on kosa8 Cloud itself.

Skipped unless KOSA8_API_KEY is set. Each check creates and deletes one
sandbox (template ``KOSA8_TEMPLATE``, default ``python``)::

    KOSA8_API_KEY=k8c_live_... uv run pytest tests/test_live_self_check.py tests/test_live.py
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from inspect_ai.util._sandbox.self_check import *

from inspect_kosa8 import Kosa8SandboxEnvironment, Kosa8SandboxEnvironmentConfig

pytestmark = pytest.mark.skipif(not os.environ.get("KOSA8_API_KEY"), reason="KOSA8_API_KEY is not set")

# kosa8 runs every command as the image's default user: checks that switch to
# another account cannot pass.
_XFAIL = {
    "test_exec_as_user": "commands run as the image's default user",
    "test_read_file_as_user": "files are read as the image's default user",
    "test_write_file_as_user": "files are written as the image's default user",
}


@pytest.fixture
async def sandbox_env(request: pytest.FixtureRequest) -> AsyncIterator[Kosa8SandboxEnvironment]:
    reason = _XFAIL.get(request.node.originalname)
    if reason:
        request.node.add_marker(pytest.mark.xfail(reason=reason, strict=False))
    cfg = Kosa8SandboxEnvironmentConfig(template=os.environ.get("KOSA8_TEMPLATE", "python"), ttl_s=1800)
    await Kosa8SandboxEnvironment.task_init("live_self_check", cfg)
    envs = await Kosa8SandboxEnvironment.sample_init("live_self_check", cfg, {})
    try:
        yield envs["default"].as_type(Kosa8SandboxEnvironment)
    finally:
        await Kosa8SandboxEnvironment.sample_cleanup("live_self_check", cfg, envs, interrupted=False)


async def test_live_state_between_commands(sandbox_env: Kosa8SandboxEnvironment) -> None:
    """Files outside /workspace and long commands, on kosa8 Cloud."""
    r = await sandbox_env.exec(["sh", "-c", "echo kept > /tmp/k8-live"])
    assert r.success, r.stderr
    r = await sandbox_env.exec(["cat", "/tmp/k8-live"])
    assert r.stdout.strip() == "kept"
    # Past the 90 s of a buffered exec: streamed, with timeout_s.
    r = await sandbox_env.exec(["sh", "-c", "sleep 95; echo done"], timeout=150)
    assert r.stdout.strip() == "done"
    with pytest.raises(TimeoutError):
        await sandbox_env.exec(["sleep", "30"], timeout=3)
    big = os.urandom(9 * 1024 * 1024)  # over the files API's 8 MiB: through exec
    await sandbox_env.write_file("big.bin", big)
    assert await sandbox_env.read_file("big.bin", text=False) == big
