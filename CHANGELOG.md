# Changelog

## 0.2.0

First release on PyPI.

- The bundled kosa8 Cloud client is brought up to the current API: commands run in the sandbox's own container where the host offers it; files under `/workspace` go through the files API (up to 8 MiB per request, larger files in parts), with permissions checked as the sandbox user; buffered exec takes `timeout_s` up to 90 s, and streamed exec is used when the host offers it.
- Typed errors, exported from `inspect_kosa8`: `Kosa8PaymentRequiredError` (402, with `upgrade_url` and `agent_message`), `Kosa8OrgFrozenError` (423), `Kosa8RateLimitError` (429, with `retry_after`), `Kosa8ExecTimeoutError` (an exec cut off by the API's time limit; still a `Kosa8TimeoutError`) and `Kosa8NotSupportedError` (501).
- `create_sandbox` checks `ttl_s` (60 s to 90 days) and takes `delete_after` (Unix ms or ISO 8601).
- New client methods, sync and async: `update_sandbox`, `list_sandboxes` / `iter_sandboxes`, `list_files`, `create_checkpoint`, `list_checkpoints`, `rewind_sandbox` and `fork_sandbox`.
- The client (`_kosa8.py`) is the same file as in `langchain-kosa8` 0.2.0.

## 0.1.0

- First version: the kosa8 Cloud Inspect AI sandbox environment (`sandbox="kosa8"`). Not published to PyPI.
