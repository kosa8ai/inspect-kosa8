# Inspect AI listing: the kosa8 sandbox in the extensions directory

Open this against [UKGovernmentBEIS/inspect_ai](https://github.com/UKGovernmentBEIS/inspect_ai)
**after** `inspect-kosa8` is on PyPI and the live suite has run on kosa8 Cloud:

```bash
KOSA8_API_KEY=k8c_live_... uv run pytest tests/test_live_self_check.py tests/test_live.py
```

The branch is `drvt69talati/inspect_ai:docs/kosa8-extension`. It adds one entry to
the `Sandboxes` category of `docs/extensions/extensions.yml`, as the boxd (#6510) and
Runtime (#6028) listings did. A docs-only change under 25 lines may be opened directly
(CONTRIBUTING.md); no issue is needed.

```yaml
- name: "[kosa8 Sandbox](https://github.com/kosa8ai/inspect-kosa8)"
  description: |
    Runs each sample in its own kosa8 Cloud microVM, deleted when the sample ends. Sandboxes can sleep and wake with their memory and files kept.
  author: "[kosa8](https://kosa8.com/?ref=inspect_ai)"
  categories: ["Sandboxes"]
```
