"""Against kosa8 Cloud itself. Skipped unless KOSA8_API_KEY is set.

Runs a one-sample eval with Inspect's mock model in a `kosa8` sandbox
(template `python`), which is created and deleted by the eval.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("KOSA8_API_KEY"), reason="KOSA8_API_KEY is not set")


def test_live_eval(tmp_path: Path) -> None:
    from inspect_ai import Task, eval
    from inspect_ai.dataset import Sample
    from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target, accuracy, scorer
    from inspect_ai.solver import Generate, TaskState, solver
    from inspect_ai.util import sandbox

    from inspect_kosa8 import Kosa8SandboxEnvironmentConfig

    @solver
    def run_in_sandbox():  # noqa: ANN202
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            await sandbox().write_file("answer.txt", "42")
            r = await sandbox().exec(["python3", "-c", "print(open('answer.txt').read())"])
            state.output.completion = r.stdout
            return state

        return solve

    @scorer(metrics=[accuracy()])
    def matches():  # noqa: ANN202
        async def score(state: TaskState, target: Target) -> Score:
            return Score(value=CORRECT if state.output.completion.strip() == target.text else INCORRECT)

        return score

    cfg = Kosa8SandboxEnvironmentConfig(template=os.environ.get("KOSA8_TEMPLATE", "python"), ttl_s=1800)
    task = Task(dataset=[Sample(input="x", target="42")], solver=run_in_sandbox(), scorer=matches(), sandbox=("kosa8", cfg))
    [log] = eval(task, model="mockllm/model", log_dir=str(tmp_path / "logs"), display="none")
    assert log.status == "success", log.error
    assert log.results is not None
    assert log.results.scores[0].metrics["accuracy"].value == 1.0
