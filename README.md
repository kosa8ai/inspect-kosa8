# inspect-kosa8

A [kosa8 Cloud](https://kosa8.com) sandbox environment for [Inspect AI](https://inspect.aisi.org.uk/). It registers the `kosa8` sandbox type through Inspect's `inspect_ai` entry point, so once the package is installed, `sandbox="kosa8"` works with no extra import.

Each sample gets its own kosa8 Cloud sandbox, a microVM created from a template when the sample starts and deleted when it ends.

## Install

```bash
pip install inspect-kosa8
```

Create an API key in the kosa8 Cloud dashboard and export it:

```bash
export KOSA8_API_KEY=k8c_live_...
```

## Usage

```python
from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import includes
from inspect_ai.solver import generate, use_tools
from inspect_ai.tool import bash, python


@task
def ctf():
    return Task(
        dataset=[Sample(input="Print the kernel version.", target="Linux")],
        solver=[use_tools([bash(), python()]), generate()],
        scorer=includes(),
        sandbox="kosa8",
    )
```

```bash
inspect eval ctf.py --model anthropic/claude-sonnet-4-6
# or choose the sandbox at the command line:
inspect eval ctf.py --sandbox kosa8
```

### Configuration

Pass a `Kosa8SandboxEnvironmentConfig`:

```python
from inspect_kosa8 import Kosa8SandboxEnvironmentConfig

Task(..., sandbox=("kosa8", Kosa8SandboxEnvironmentConfig(template="python", region="eu-hel1")))
```

or the path to a YAML or JSON file with the same fields:

```yaml
# kosa8.yaml
template: python
ttl_s: 7200
```

```python
Task(..., sandbox=("kosa8", "kosa8.yaml"))
```

| Field | Default | Meaning |
|---|---|---|
| `template` | `"python"` | kosa8 Cloud template key (`GET /v1/templates` lists what your org can use). |
| `region` | org default | Region id, such as `eu-hel1`. |
| `sleep_after_s` | org default | Idle seconds before the sandbox sleeps (`0` = never). The next command wakes it. |
| `ttl_s` | `14400` | Seconds until kosa8 Cloud deletes the sandbox even if cleanup never runs. |
| `workspace` | `/workspace` | Per-sample working directory; relative paths in `exec(cwd=)`, `read_file()` and `write_file()` resolve against it. |
| `persist_dirs` | `("/tmp", "/var/tmp")` | Directories outside the workspace kept on the workspace disk between commands (see below). |
| `bash_fallback` | `True` | Run `bash ...` commands with `/bin/sh` when the image has no bash (see below). |
| `shell` | `"auto"` | Shell that wraps each command: bash when the image has it, else `/bin/sh`. |
| `stream` | `None` | `None`: streamed exec when the host offers it, else buffered. `False`: buffered only. `True`: require streaming. |
| `files_api` | `True` | Move files under `/workspace` through the kosa8 files API; `False` sends every file through exec. |
| `base_url` | `KOSA8_CLOUD_URL` or `https://api.kosa8.com` | API address. |

Concurrency: kosa8 Cloud limits how many sandboxes an org runs at once. The provider's default `max_sandboxes` is 10 (or `KOSA8_MAX_SANDBOXES`); set `--max-sandboxes` to match your org's limit. Creates that find the org at its limit, or no host with room, are retried with backoff up to five times.

### Cleanup

Sandboxes are deleted at the end of each sample, and at the end of the task for samples that were interrupted. With `--no-sandbox-cleanup` the sandbox ids are printed instead. Every sandbox this package creates is recorded in `~/.cache/inspect-kosa8/sandboxes/` until it is deleted, so

```bash
inspect sandbox cleanup kosa8            # every sandbox recorded on this machine
inspect sandbox cleanup kosa8 sbx_...    # one sandbox
```

deletes what is left. `ttl_s` is a backstop for anything else.

## What is supported

| `SandboxEnvironment` method | Support |
|---|---|
| `exec(cmd, input, cwd, env, timeout)` | Yes. `cmd[0]` is resolved through the image's `PATH`, `env` is passed through the exec API, `input` becomes stdin, `timeout` is enforced inside the sandbox with `/usr/bin/timeout -s KILL` and by the API's own `timeout_s`, and raises `TimeoutError`. A command that is not executable raises `PermissionError`. Output over Inspect's limit raises `OutputLimitExceededError`. A sandbox that is gone or stopped raises `SandboxUnavailableError`. |
| `exec(user=...)` | `root` (or `0`) when the image's default user is root, which it is for the kosa8 templates. Commands always run as the image's default user, so any other `user` raises `SandboxUserUnsupportedError`. |
| `exec(timeout_retry=...)` | Accepted; commands are not retried. |
| `write_file`, `read_file` | Yes, text and binary, creating parent directories. Raise `FileNotFoundError`, `IsADirectoryError`, `PermissionError`, `UnicodeDecodeError` and `OutputLimitExceededError` as Inspect specifies. Text keeps newlines as written. |
| `read_file(user=...)`, `write_file(user=...)` | As for `exec`: `root` when the default user is root, otherwise `SandboxUserUnsupportedError`. |
| `connection()` | No. |
| Dockerfile / `compose.yaml` configs | No. kosa8 Cloud runs its templates and does not build images, so the provider is not Docker-compatible. |

## How it works, and the limits that follow

The package talks to the kosa8 Cloud REST API with `httpx`: create, exec (buffered and streamed), the files API, sleep, wake and delete.

- **State between commands.** Commands run in the sandbox's own container, so files written anywhere (`/tmp`, `/root`, `/workspace`) are there for the next command, and survive sleep and wake. Commands start in `/workspace`. For hosts that start each command in a fresh container, `/tmp` and `/var/tmp` are also bind-mounted or linked onto `/workspace/.kosa8-persist/` (`persist_dirs`); on a long-lived container this is a no-op after the first command.
- **Inspect's `bash()` and `python()` tools** call `bash --login -c`. Templates whose image has no bash (the Alpine-based ones) run such commands with `/bin/sh` instead when `bash_fallback` is on (the default). That covers ordinary shell commands; scripts that rely on bash-only syntax need a template with bash.
- **Files.** Files under `/workspace` of up to 8 MiB go through the kosa8 files API. Larger files, files elsewhere, hosts without the files API, and every error (so that `FileNotFoundError`, `IsADirectoryError` and `PermissionError` are reported precisely) go through exec, base64-encoded in chunks: about 36 KB per upload request (the API accepts 64 KiB of JSON per request) and 512 KiB per download request. The image needs `sh`, `base64` and `dd`, which the kosa8 templates have.
- **Long commands.** A buffered exec is held to 90 seconds. When the host offers streamed exec (kosa8 Cloud does), the package streams every command, so commands run to their `timeout` with no limit of their own. A command without a `timeout` on a host that only buffers raises `TimeoutError` at 90 seconds.
- **Large commands and stdin.** Each exec argument is at most 8 KiB, so the command and its stdin travel in environment variables, and anything over the 64 KiB request is written to `/workspace/.kosa8-run/` first and read from there.
- **Output.** A buffered exec keeps the first and last 1 MiB of each stream; a streamed one keeps all of it.

Not covered by this package: kosa8's snapshot, checkpoint, rewind and fork endpoints, and `connection()`.

## Tests

```bash
uv sync --group test
uv run pytest
```

The tests run against an in-process fake of the kosa8 Cloud API (`tests/fake_cloud.py`) that executes each command for real, with the files API on and off. They include Inspect's portable sandbox checks (`inspect_ai.util._sandbox.self_check`) and a full `inspect_ai.eval()` with the mock model, and need no network or key. Lint and types:

```bash
uv sync --group dev
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Optional runs:

```bash
# the transport in a Linux userland like the python template
kosa8 run -d --name k8test python:3.12-alpine sleep 3600
KOSA8_TEST_CONTAINER=k8test uv run pytest tests/test_transport.py

# on kosa8 Cloud itself: Inspect's portable checks and a one-sample eval
# (each check creates and deletes one sandbox)
KOSA8_API_KEY=k8c_live_... uv run pytest tests/test_live_self_check.py tests/test_live.py
```

## License

MIT
