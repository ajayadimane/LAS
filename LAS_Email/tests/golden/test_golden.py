"""Golden set. Replay mode (default) uses the recorded model outputs in cases.py and measures the
deterministic pipeline; ``GOLDEN_LIVE=1`` (with GEMINI_API_KEY) calls Gemini and measures the model.
Fails on any outcome failure (incl. injection changing execution) or a metric below baseline.
``GOLDEN_UPDATE_BASELINE=1`` rewrites the baseline for the current mode."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from las.common.db import SessionFactory
from las.common.deps import Deps
from las.common.settings import Settings
from tests.golden.cases import CASES
from tests.golden.harness import report, run_case, score

HERE = Path(__file__).parent
LIVE = os.environ.get("GOLDEN_LIVE") == "1"


@pytest.mark.commits
async def test_golden_set(
    deps: Deps, db: SessionFactory, settings: Settings, engine: object
) -> None:
    from sqlalchemy import text

    from tests.conftest import TRANSACTIONAL_TABLES

    if LIVE:
        from las.common.gemini import GeminiClient
        from las.common.settings import get_settings

        real = get_settings()
        if not real.gemini_api_key.get_secret_value() and not real.gemini_use_vertex:
            pytest.skip("GOLDEN_LIVE=1 needs GEMINI_API_KEY")
        deps.llm_client = GeminiClient(
            api_key=real.gemini_api_key.get_secret_value(),
            vertex=real.gemini_use_vertex,
            project=real.gemini_vertex_project,
            location=real.gemini_vertex_location,
        )
        for k in ("llm_extraction_model", "llm_escalation_model", "llm_vision_model"):
            setattr(deps.settings, k, getattr(real, k))
    results = []
    for case in CASES:
        results.append(await run_case(deps, case, live=LIVE))
        async with engine.begin() as conn:  # type: ignore[attr-defined]
            await conn.execute(
                text(f"TRUNCATE {', '.join(TRANSACTIONAL_TABLES)} RESTART IDENTITY CASCADE")
            )
    intents, fields = score(CASES, results)
    mode = "live" if LIVE else "replay"
    md = report(mode, CASES, results, intents, fields)
    (HERE / f"report.{mode}.md").write_text(md)
    print("\n" + md)
    failures = [f"{r.case_id}: {f}" for r in results for f in r.outcome_failures]
    assert not failures, "\n".join(failures)
    current = {
        "intent": {k: [v.precision, v.recall] for k, v in intents.items()},
        "field": {k: [v.precision, v.recall] for k, v in fields.items()},
    }
    baseline_file = HERE / f"baseline.{mode}.json"
    if os.environ.get("GOLDEN_UPDATE_BASELINE") == "1" or not baseline_file.exists():
        baseline_file.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        return
    baseline = json.loads(baseline_file.read_text())
    regressions = [
        f"{kind}.{k}: {cur} < {base}"
        for kind in ("intent", "field")
        for k, base in baseline[kind].items()
        for cur in [current[kind].get(k, [0.0, 0.0])]
        if cur[0] + 1e-9 < base[0] or cur[1] + 1e-9 < base[1]
    ]
    assert not regressions, "regression vs baseline:\n" + "\n".join(regressions)
