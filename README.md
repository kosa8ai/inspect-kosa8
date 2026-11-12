# inspect-kosa8

A [kosa8 Cloud](https://kosa8.com) sandbox environment for [Inspect AI](https://inspect.aisi.org.uk/). It registers the `kosa8` sandbox type through Inspect's `inspect_ai` entry point, so once the package is installed, `sandbox="kosa8"` works with no extra import.

Each sample gets its own kosa8 Cloud sandbox, created from a template when the sample starts and deleted when it ends.

## Install

`inspect-kosa8` is not on PyPI yet. Install it from GitHub until the PyPI release:

```bash
pip install "git+https://github.com/kosa8ai/inspect-kosa8"
```

```bash
uv pip install "git+https://github.com/kosa8ai/inspect-kosa8"
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
| `exec(cmd, input, cwd, env, timeout)` | Yes. `cmd[0]` is resolved through the image's `PATH`, `env` is passed through the exec API, `input` becomes stdin, `timeout` is enforced inside the sandbox with `timeout -s KILL` and raises `TimeoutError`. Output over Inspect's limit raises `OutputLimitExceededError`. |
| `exec(user=...)` | No. Commands run as the image's default user; any `user` raises `SandboxUserUnsupportedError`. |
| `exec(timeout_retry=...)` | Accepted; commands are not retried. |
| `write_file`, `read_file` | Yes, text and binary, creating parent directories. Raise `FileNotFoundError`, `IsADirectoryError`, `PermissionError`, `UnicodeDecodeError` and `OutputLimitExceededError` as Inspect specifies. Text keeps newlines as written. |
| `read_file(user=...)`, `write_file(user=...)` | No; raise `SandboxUserUnsupportedError`. |
| `connection()` | No. |
| Dockerfile / `compose.yaml` configs | No. kosa8 Cloud runs its templates and does not build images, so the provider is not Docker-compatible. |

## kosa8 Cloud basics

- The address is `https://api.kosa8.com` (`KOSA8_CLOUD_URL` overrides it; https only, except localhost).
- The `base` template is **Alpine Linux**: `sh` is busybox, and there is no `bash`, `python` or `git`. The `python` template adds Python 3. `GET /v1/templates` lists what your org can start.
- Each command runs in a fresh container; **only `/workspace` persists** between commands (this package also keeps `/tmp`, see below). Install packages into `/workspace`.
- A buffered exec is cut off after about **30 seconds**; streamed exec, used when the host offers it, is not.
- Errors from the bundled client (`inspect_kosa8._kosa8`) are typed, all subclasses of `Kosa8Error` with `status` and `code`: `Kosa8PaymentRequiredError` (402; show `upgrade_url`, and an agent should relay `agent_message` and stop), `Kosa8OrgFrozenError` (423 `org_frozen`: an owner or admin must unfreeze the org), `Kosa8RateLimitError` (429, `retry_after`), `Kosa8ExecTimeoutError` (an exec cut off by the 30 s limit; also a `Kosa8TimeoutError`) and `Kosa8NotSupportedError` (501).
- The bundled client also covers `update_sandbox` (PATCH lifetime), `list_sandboxes`/`iter_sandboxes` (with `limit`/`cursor` paging when the server offers it), and the data-plane calls `read_file`, `write_file`, `list_files`, `create_checkpoint`, `list_checkpoints`, `rewind_sandbox` and `fork_sandbox`. The data-plane calls may answer 501 `not_supported` on current hosts, so this package's own behaviour does not depend on them.

## How it works, and the limits that follow

The package uses only the kosa8 Cloud calls every host offers today: create, exec, sleep, wake and delete.

- **Each command runs in a fresh container** from the template image, over the sandbox's persistent `/workspace` disk. Files under `/workspace` carry from one command to the next; files written elsewhere do not. Commands start in `/workspace`. So that tools which write to `/tmp` keep working, `/tmp` and `/var/tmp` are bind-mounted or linked onto `/workspace/.kosa8-persist/` before each command; add more with `persist_dirs`. System-wide installs (`apk add`, `pip install` into site-packages) do not persist; install into `/workspace` or use a template that has what the eval needs.
- **Inspect's `bash()` and `python()` tools** call `bash --login -c`. The kosa8 templates are Alpine-based and have no bash, so with `bash_fallback` (the default) such commands run with `/bin/sh` (busybox ash) instead. That covers ordinary shell commands; scripts that rely on bash-only syntax need a template whose image has bash. Inspect's injected sandbox tools keep state under `/var/tmp`, which is why `/var/tmp` is persisted by default; they have not been tested on kosa8 Cloud yet.
- **Files travel through exec**, base64-encoded in chunks: about 36 KB per upload request (the API accepts 64 KiB of JSON per request) and 512 KiB per download request. The image needs `sh`, `base64` and `dd`, which the kosa8 templates have.
- **Long commands.** A buffered exec has to finish within the host's request time, about 30 seconds; past that the command raises `TimeoutError`. When a host offers streamed exec the package uses it and commands run to their `timeout`.
- **Output.** A buffered exec keeps the first and last 1 MiB of each stream.

Not used by this package: kosa8's checkpoint, rewind, fork and native file endpoints. They need a host with the kosa8 data plane, which not every production host offers yet. The bundled client has methods for them if you want to call them yourself.

## Tests

```bash
uv sync --group test
uv run pytest
```

The tests run against an in-process fake of the kosa8 Cloud API (`tests/fake_cloud.py`) that executes each command for real, including a full `inspect_ai.eval()` with the mock model, so they need no network or key. Optional runs:

```bash
# the transport in a Linux userland like the python template
kosa8 run -d --name k8test python:3.12-alpine sleep 3600
KOSA8_TEST_CONTAINER=k8test uv run pytest tests/test_transport.py

# a one-sample eval on kosa8 Cloud itself (creates and deletes one sandbox)
KOSA8_API_KEY=k8c_live_... uv run pytest tests/test_live.py
```

## License

MIT
