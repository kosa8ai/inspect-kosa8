"""kosa8 Cloud sandbox environment for Inspect AI."""

from __future__ import annotations

import asyncio
import os
import shlex
from contextvars import ContextVar
from logging import getLogger
from pathlib import Path
from typing import Literal, Optional, Union, overload

from inspect_ai.util import (
    ExecResult,
    OutputLimitExceededError,
    SandboxEnvironment,
    SandboxEnvironmentConfigType,
    SandboxEnvironmentLimits,
    sandboxenv,
    trace_message,
)
from pydantic import BaseModel, ConfigDict

from inspect_kosa8._kosa8 import (
    DEFAULT_WORKSPACE,
    AsyncKosa8Client,
    Kosa8Error,
    Kosa8TimeoutError,
    SandboxSession,
    adrive,
)

try:  # inspect_ai >= 0.3.2xx
    from inspect_ai.util import SandboxUserUnsupportedError
except ImportError:  # pragma: no cover - older inspect_ai
    class SandboxUserUnsupportedError(RuntimeError):  # type: ignore[no-redef]
        pass

logger = getLogger(__name__)

TRACE = "kosa8"
_BASH_NAMES = ("bash", "/bin/bash", "/usr/bin/bash")
CREATE_ATTEMPTS = 5
_RETRYABLE_CREATE = {"no_capacity", "quota_exceeded"}


class Kosa8SandboxEnvironmentConfig(BaseModel):
    """Settings for the ``kosa8`` sandbox.

    Pass an instance as the sandbox config, for example
    ``sandbox=("kosa8", Kosa8SandboxEnvironmentConfig(template="python"))``,
    or the path to a YAML or JSON file with these fields.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    template: str = "python"
    """kosa8 Cloud template key (``GET /v1/templates``)."""
    region: Optional[str] = None
    """Region id such as ``eu-hel1``; the org default when unset."""
    sleep_after_s: Optional[int] = None
    """Idle seconds before the sandbox sleeps (0 = never)."""
    ttl_s: Optional[int] = 4 * 3600
    """Seconds until kosa8 Cloud deletes the sandbox even if cleanup never runs."""
    workspace: str = DEFAULT_WORKSPACE
    """Per-sample working directory; relative paths resolve against it."""
    persist_dirs: tuple[str, ...] = ("/tmp", "/var/tmp")
    """Directories outside the workspace kept on the workspace disk between commands."""
    bash_fallback: bool = True
    """Run ``bash ...`` commands with /bin/sh when the image has no bash (Inspect's
    bash() and python() tools call ``bash --login -c``; Alpine images lack bash)."""
    shell: str = "auto"
    """Shell that runs each command: bash if the image has it, else /bin/sh."""
    stream: Optional[bool] = None
    """None: streamed exec when the host offers it; False: buffered only; True: require streaming."""
    base_url: Optional[str] = None
    """API address; defaults to KOSA8_CLOUD_URL or https://api.kosa8.com."""


def _load_config(config: SandboxEnvironmentConfigType | None) -> Kosa8SandboxEnvironmentConfig:
    if config is None:
        return Kosa8SandboxEnvironmentConfig()
    if isinstance(config, Kosa8SandboxEnvironmentConfig):
        return config
    if isinstance(config, BaseModel):
        return Kosa8SandboxEnvironmentConfig.model_validate(config.model_dump())
    if isinstance(config, str):
        name = os.path.basename(config).lower()
        if name == "dockerfile" or "compose" in name:
            raise ValueError(
                "The kosa8 sandbox runs kosa8 Cloud templates and does not build Dockerfiles or "
                "compose files. Pass a Kosa8SandboxEnvironmentConfig (or a YAML/JSON file of one) instead."
            )
        path = Path(config)
        if not path.is_file():
            raise ValueError(f"kosa8 sandbox config file not found: {config}")
        text = path.read_text()
        if path.suffix.lower() == ".json":
            return Kosa8SandboxEnvironmentConfig.model_validate_json(text)
        import yaml  # inspect_ai depends on pyyaml

        return Kosa8SandboxEnvironmentConfig.model_validate(yaml.safe_load(text) or {})
    raise ValueError(f"Unsupported kosa8 sandbox config: {config!r}")


def _state_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "inspect-kosa8" / "sandboxes"


def _remember(sandbox_id: str) -> None:
    try:
        d = _state_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / sandbox_id).touch()
    except OSError:
        pass


def _forget(sandbox_id: str) -> None:
    try:
        (_state_dir() / sandbox_id).unlink(missing_ok=True)
    except OSError:
        pass


def _remembered() -> list[str]:
    d = _state_dir()
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


_running: ContextVar[list[str]] = ContextVar("kosa8_running_sandboxes")


def _running_list() -> list[str]:
    try:
        return _running.get()
    except LookupError:
        lst: list[str] = []
        _running.set(lst)
        return lst


@sandboxenv(name="kosa8")
class Kosa8SandboxEnvironment(SandboxEnvironment):
    """A kosa8 Cloud sandbox for each Inspect sample.

    Each sample gets its own kosa8 Cloud sandbox, created from a template and
    deleted when the sample ends. ``exec``, ``read_file`` and ``write_file``
    go through the kosa8 Cloud exec API; see the package README for how files
    and long commands are handled.
    """

    def __init__(self, sandbox_id: str, client: AsyncKosa8Client, config: Kosa8SandboxEnvironmentConfig) -> None:
        super().__init__()
        self.sandbox_id = sandbox_id
        self.client = client
        self.config = config
        self.session = SandboxSession(workspace=config.workspace, persist_dirs=config.persist_dirs, shell=config.shell)

    # Lifecycle ---------------------------------------------------------------

    @classmethod
    def config_files(cls) -> list[str]:
        return []

    @classmethod
    def is_docker_compatible(cls) -> bool:
        return False

    @classmethod
    def default_concurrency(cls) -> int | None:
        # kosa8 Cloud limits how many sandboxes an org runs at once; set
        # --max-sandboxes (or KOSA8_MAX_SANDBOXES) to your org's limit.
        try:
            return int(os.environ.get("KOSA8_MAX_SANDBOXES", "10"))
        except ValueError:
            return 10

    @classmethod
    def config_deserialize(cls, config: dict) -> BaseModel:  # type: ignore[type-arg]
        return Kosa8SandboxEnvironmentConfig(**config)

    @classmethod
    async def task_init(cls, task_name: str, config: SandboxEnvironmentConfigType | None) -> None:
        _load_config(config)  # fail fast on a bad config
        _running.set([])

    @classmethod
    async def sample_init(
        cls,
        task_name: str,
        config: SandboxEnvironmentConfigType | None,
        metadata: dict[str, str],
    ) -> dict[str, SandboxEnvironment]:
        cfg = _load_config(config)
        client = _make_client(cfg)
        box = await _create_with_retry(client, cfg)
        sandbox_id = box["id"]
        _remember(sandbox_id)
        _running_list().append(sandbox_id)
        trace_message(logger, TRACE, f"created kosa8 sandbox {sandbox_id} for task {task_name}")
        env = cls(sandbox_id, client, cfg)
        try:
            if cfg.workspace != DEFAULT_WORKSPACE:
                r = await env.exec(["mkdir", "-p", cfg.workspace], cwd="/")
                if not r.success:
                    raise RuntimeError(f"could not create the workspace {cfg.workspace}: {r.stderr}")
        except BaseException:
            await _delete_quietly(client, sandbox_id)
            raise
        return {"default": env}

    @classmethod
    async def sample_cleanup(
        cls,
        task_name: str,
        config: SandboxEnvironmentConfigType | None,
        environments: dict[str, SandboxEnvironment],
        interrupted: bool,
    ) -> None:
        if interrupted:
            return  # task_cleanup deletes them
        for env in environments.values():
            k = env.as_type(Kosa8SandboxEnvironment)
            await _delete_quietly(k.client, k.sandbox_id)
            running = _running_list()
            if k.sandbox_id in running:
                running.remove(k.sandbox_id)
            await k.client.aclose()

    @classmethod
    async def task_cleanup(cls, task_name: str, config: SandboxEnvironmentConfigType | None, cleanup: bool) -> None:
        running = list(_running_list())
        if not running:
            return
        if not cleanup:
            print("kosa8 sandboxes left running (--no-sandbox-cleanup):")
            for sid in running:
                print(f"  {sid}")
            print("Delete them with: inspect sandbox cleanup kosa8 <id>   (or without an id for all of them)")
            return
        cfg = _load_config(config)
        async with _make_client(cfg) as client:
            for sid in running:
                await _delete_quietly(client, sid)
        _running.set([])

    @classmethod
    async def cli_cleanup(cls, id: str | None) -> None:  # noqa: A002 (Inspect's name)
        ids = [id] if id else _remembered()
        if not ids:
            print("No kosa8 sandboxes recorded by inspect-kosa8 on this machine.")
            return
        async with _make_client(Kosa8SandboxEnvironmentConfig()) as client:
            for sid in ids:
                await _delete_quietly(client, sid)
                print(f"deleted {sid}")

    # Instance methods --------------------------------------------------------

    def _check_user(self, user: str | None) -> None:
        if user is not None:
            raise SandboxUserUnsupportedError(
                f"the kosa8 sandbox runs commands as the image's default user and cannot run as {user!r}"
            )

    def _abs(self, file: str) -> str:
        return file if file.startswith("/") else f"{self.config.workspace.rstrip('/')}/{file}"

    async def exec(
        self,
        cmd: list[str],
        input: str | bytes | None = None,  # noqa: A002 (Inspect's name)
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        user: str | None = None,
        timeout: int | None = None,  # noqa: ASYNC109
        timeout_retry: bool = True,
        concurrency: bool = True,
    ) -> ExecResult[str]:
        self._check_user(user)
        if not cmd:
            raise ValueError("cmd must not be empty")
        stdin = input.encode("utf-8") if isinstance(input, str) else input
        command = "exec " + shlex.join(cmd)
        if self.config.bash_fallback and cmd[0] in _BASH_NAMES:
            command = (
                f"if command -v bash >/dev/null 2>&1; then {command}; "
                f"else exec {shlex.join(['/bin/sh', *cmd[1:]])}; fi"
            )
        op = self.session.command_op(
            command,
            cwd=cwd,
            env=env,
            timeout=timeout,
            stdin=stdin,
        )
        try:
            out = await adrive(op, lambda req: self.client.exec(self.sandbox_id, req))
        except Kosa8TimeoutError as e:
            raise TimeoutError(f"kosa8 sandbox command timed out: {e}") from e
        if out.timed_out:
            raise TimeoutError(f"Command timed out after {timeout} seconds")
        limit = SandboxEnvironmentLimits.MAX_EXEC_OUTPUT_SIZE
        for stream in (out.stdout, out.stderr):
            if len(stream.encode("utf-8")) > limit:
                raise OutputLimitExceededError(
                    limit_str=SandboxEnvironmentLimits.MAX_EXEC_OUTPUT_SIZE_STR,
                    truncated_output=stream[-limit:],
                )
        rc = out.exit_code if out.exit_code is not None else 1
        return ExecResult(success=rc == 0, returncode=rc, stdout=out.stdout, stderr=out.stderr)

    async def write_file(self, file: str, contents: str | bytes, *, user: str | None = None) -> None:
        self._check_user(user)
        path = self._abs(file)
        data = contents.encode("utf-8") if isinstance(contents, str) else contents
        err = await adrive(self.session.upload_op(path, data), lambda req: self.client.exec(self.sandbox_id, req))
        if err is None:
            return
        if err == "is_directory":
            raise IsADirectoryError(f"Is a directory: {file}")
        if err == "permission_denied":
            raise PermissionError(f"Permission denied: {file}")
        if err == "invalid_path":
            raise NotADirectoryError(f"A parent of {file} is not a directory")
        raise RuntimeError(f"kosa8 sandbox could not write {file}: {err}")

    @overload
    async def read_file(self, file: str, text: Literal[True] = True, *, user: str | None = None) -> str: ...

    @overload
    async def read_file(self, file: str, text: Literal[False], *, user: str | None = None) -> bytes: ...

    async def read_file(self, file: str, text: bool = True, *, user: str | None = None) -> Union[str, bytes]:
        self._check_user(user)
        path = self._abs(file)
        got = await adrive(
            self.session.download_op(path, max_bytes=SandboxEnvironmentLimits.MAX_READ_FILE_SIZE),
            lambda req: self.client.exec(self.sandbox_id, req),
        )
        if got.error is not None:
            if got.error == "file_not_found":
                raise FileNotFoundError(f"No such file: {file}")
            if got.error == "is_directory":
                raise IsADirectoryError(f"Is a directory: {file}")
            if got.error == "permission_denied":
                raise PermissionError(f"Permission denied: {file}")
            if got.error == "too_large":
                raise OutputLimitExceededError(limit_str=SandboxEnvironmentLimits.MAX_READ_FILE_SIZE_STR, truncated_output=None)
            raise RuntimeError(f"kosa8 sandbox could not read {file}: {got.error}")
        data = got.content or b""
        # Text keeps newlines as they are (like open(newline="")).
        return data.decode("utf-8") if text else data


def _make_client(cfg: Kosa8SandboxEnvironmentConfig) -> AsyncKosa8Client:
    """The API client for a config (tests replace this)."""
    return AsyncKosa8Client(base_url=cfg.base_url, stream=cfg.stream)


async def _create_with_retry(client: AsyncKosa8Client, cfg: Kosa8SandboxEnvironmentConfig) -> dict:  # type: ignore[type-arg]
    delay = 2.0
    for attempt in range(1, CREATE_ATTEMPTS + 1):
        try:
            return await client.create_sandbox(
                template=cfg.template, region=cfg.region, sleep_after_s=cfg.sleep_after_s, ttl_s=cfg.ttl_s
            )
        except Kosa8Error as e:
            if e.code not in _RETRYABLE_CREATE or attempt == CREATE_ATTEMPTS:
                raise
            trace_message(logger, TRACE, f"kosa8 create answered {e.code}; retrying in {delay:.0f} s")
            await asyncio.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")


async def _delete_quietly(client: AsyncKosa8Client, sandbox_id: str) -> None:
    try:
        await client.delete_sandbox(sandbox_id)
    except Kosa8Error as e:
        logger.warning(f"could not delete kosa8 sandbox {sandbox_id}: {e}")
        return
    _forget(sandbox_id)
