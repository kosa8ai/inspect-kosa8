# Inspect AI docs PR: list the kosa8 sandbox

Open this against [UKGovernmentBEIS/inspect_ai](https://github.com/UKGovernmentBEIS/inspect_ai)
**after** `inspect-kosa8` is on PyPI and one live eval has run on kosa8 Cloud
(`KOSA8_API_KEY=... uv run pytest tests/test_live.py`).

## Changes

### `docs/sandboxing.qmd`, the sandbox table

Add after the `openshell` row (before `local`):

```markdown
| `kosa8` | [inspect-kosa8](https://pypi.org/project/inspect-kosa8/) | No | [kosa8 Cloud](https://github.com/kosa8ai/inspect-kosa8) microVM sandboxes that can sleep and wake with their files kept. |
```

The third column ("Docker compatible") is `No`: kosa8 runs its own templates
and does not build Dockerfiles or compose files.

### `docs/extensions/extensions.yml`

Add after the other `Sandboxes` entries:

```yaml
- name: "[kosa8 Sandbox](https://github.com/kosa8ai/inspect-kosa8)"
  description: |
    Sandbox environment for Inspect using kosa8 Cloud microVM sandboxes.
  author: "[kosa8](https://github.com/kosa8ai/inspect-kosa8)"
  categories: ["Sandboxes"]
```

## PR title

docs: add kosa8 sandbox (inspect-kosa8) to sandbox listings

## PR description

Adds `inspect-kosa8` to the sandbox environment table in `sandboxing.qmd` and
to the extensions listing.

`inspect-kosa8` registers a `kosa8` sandbox type through the `inspect_ai` entry
point, so `sandbox="kosa8"` works once the package is installed. Each sample
gets its own kosa8 Cloud sandbox, created in `sample_init` and deleted in
`sample_cleanup`; `inspect sandbox cleanup kosa8` removes any left behind.

It implements `exec` (with `input`, `cwd`, `env`, `timeout` and the documented
`TimeoutError`/`OutputLimitExceededError` behaviour), `write_file` and
`read_file` (with the documented exceptions). `user=` raises
`SandboxUserUnsupportedError`. It is not Docker-compatible, so the table
marks it `No`.

- Package: https://pypi.org/project/inspect-kosa8/
- Source and docs: https://github.com/kosa8ai/inspect-kosa8

🤖 Generated with [Claude Code](https://claude.com/claude-code)
