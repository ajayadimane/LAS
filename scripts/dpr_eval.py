#!/usr/bin/env python
"""Evaluate the DPR analysis pipeline on real DPRs.

Runs exactly what the worker runs -- document_input.prepare_document()
(LiteParse, Gemini vision for weak pages, classification), then
parallel_graph.analyze_parallel() and the CAM snapshot's readiness -- on
every PDF under --data-dir, and scores each result against its annotation
(see app/services/dpr_agentic/evaluation.py for the format).

The bank's decision comes from the folder: a parent folder whose name
contains "reject" means rejected, anything else approved.

    # Show what would run and the estimated cost; makes no calls:
    python scripts/dpr_eval.py

    # Live run (paid Gemini calls), recording responses for replay:
    python scripts/dpr_eval.py --yes --record

    # Re-score offline from the recordings (no network, no cost):
    python scripts/dpr_eval.py --replay

    # First-draft annotations from a run, for a credit person to correct:
    python scripts/dpr_eval.py --yes --draft-annotations

Privacy: DPRs are real customer documents. Results, cassettes and the
id -> filename manifest stay under eval/ in gitignored folders; reports and
annotations name documents only by a sha256 prefix.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402
import yaml  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis  # noqa: E402
from app.services.dpr_agentic.document_input import (  # noqa: E402
    ClassificationNotReady,
    prepare_document,
)
from app.services.dpr_agentic.document_pipeline.liteparse_client import (  # noqa: E402
    LiteParseClient,
)
from app.services.dpr_agentic.evaluation import (  # noqa: E402
    DEFAULT_PRICING,
    DEFAULT_USD_INR,
    draft_annotation,
    score_document,
    stability,
    usage_summary,
)
from app.services.dpr_agentic.gemini import (  # noqa: E402
    CURRENT_RUN_ID,
    CURRENT_RUN_USAGE,
    GeminiAgentClient,
)
from app.services.dpr_agentic.gemini_cassette import (  # noqa: E402
    RecordingTransport,
    ReplayTransport,
)
from app.services.dpr_agentic.grounding import FileGroundingCache  # noqa: E402
from app.services.dpr_agentic.ocr_cache import FileOcrCache  # noqa: E402
from app.services.dpr_agentic.parallel_graph import analyze_parallel  # noqa: E402
from app.services.dpr_agentic.schemas import AnalysisInput, SourcePage  # noqa: E402

EVAL_DIR = REPO_ROOT / "eval"
DEFAULT_DATA_DIR = REPO_ROOT.parent / "DPRData"
# Rough planning figure only (pre-change baseline ~77k tokens on Flash);
# the real cost of each run is measured and reported.
ESTIMATED_INR_PER_RUN = 15.0


def discover(data_dir: Path) -> list[dict]:
    docs = []
    for path in sorted(data_dir.rglob("*.pdf")):
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        label = "rejected" if "reject" in path.parent.name.lower() else "approved"
        docs.append({"id": sha[:12], "sha256": sha, "label": label, "path": path})
    return docs


def select(docs: list[dict], only: list[str] | None) -> list[dict]:
    if not only:
        return docs
    return [
        d for d in docs if any(d["id"].startswith(o) or d["label"] == o for o in only)
    ]


def load_annotation(doc_id: str) -> dict | None:
    path = EVAL_DIR / "annotations" / f"{doc_id}.yaml"
    return yaml.safe_load(path.read_text()) if path.exists() else None


def build_gemini(
    settings, model: str, transport
) -> tuple[httpx.AsyncClient, GeminiAgentClient]:
    http = httpx.AsyncClient(timeout=120.0, transport=transport)
    client = GeminiAgentClient(
        http,
        settings.dpr_agentic_gemini_api_key.get_secret_value()
        if settings.dpr_agentic_gemini_api_key
        else "replay-only",
        model,
        thinking_budget=settings.dpr_agentic_thinking_budget,
        thinking_level=settings.dpr_agentic_thinking_level,
        max_output_tokens=settings.dpr_agentic_max_output_tokens,
    )
    return http, client


async def run_once(
    doc: dict, repeat: int, gemini, liteparse, settings, out_dir: Path
) -> dict:
    run_id = f"eval-{doc['id']}-r{repeat}"
    usage: list[dict] = []
    usage_token = CURRENT_RUN_USAGE.set(usage)
    run_token = CURRENT_RUN_ID.set(run_id)
    started = time.monotonic()
    record: dict = {"doc_id": doc["id"], "repeat": repeat, "label": doc["label"]}
    try:
        data = doc["path"].read_bytes()
        prepared = await prepare_document(
            data,
            doc["path"].name,
            settings=settings,
            gemini=gemini,
            liteparse=liteparse,
            run_id=run_id,
            document_sha256=doc["sha256"],
            ocr_cache=FileOcrCache(EVAL_DIR / "ocr_cache"),
        )
        payload = AnalysisInput(
            service_request_id=f"eval:{doc['id']}",
            wallet_document_id=f"eval:{doc['id']}",
            document_sha256=doc["sha256"],
            pages=[SourcePage(**p) for p in prepared.pages],
        )
        result = await analyze_parallel(
            payload,
            gemini,
            thread_id=run_id,
            cam_engine_enabled=settings.cam_engine_enabled,
            grounding_enabled=settings.dpr_agentic_grounding_enabled,
            grounding_cache=FileGroundingCache(EVAL_DIR / "grounding_cache"),
        )
        snapshot = build_snapshot_from_analysis(
            result, report_id=run_id, report_version=1, analysis_run_id=run_id
        )
        result_json = result.model_dump(mode="json")
        (out_dir / f"{run_id}.json").write_text(json.dumps(result_json, indent=1))
        record.update(
            status="completed",
            pages=len(prepared.pages),
            vision_pages=len(prepared.vision_pages),
            readiness=snapshot.readiness_assessment.status.value,
            result=result_json,
        )
    except ClassificationNotReady as exc:
        record.update(status="not_a_dpr", error=f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001 - one document must not stop the run
        record.update(status="failed", error=type(exc).__name__)
    finally:
        CURRENT_RUN_USAGE.reset(usage_token)
        CURRENT_RUN_ID.reset(run_token)
    record["latency_s"] = round(time.monotonic() - started, 1)
    record["usage"] = usage
    return record


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


COLUMNS = (
    "Doc",
    "Bank",
    "Status",
    "Readiness",
    "Must-fix",
    "Findings",
    "Extraction",
    "Must-find",
    "Violations",
    "Topic agreement (all / must-fix)",
    "Checklist (answers / flagged)",
    "Tokens in/out",
    "INR",
    "Latency s",
)


def write_report(out_dir: Path, rows: list[dict], meta: dict) -> Path:
    lines = [
        f"# DPR evaluation {meta['started']}",
        "",
        f"Model `{meta['model']}`, mode `{meta['mode']}`, repeats {meta['repeat']}, "
        f"{len(rows)} documents. Costs use indicative prices "
        f"(USD->INR {meta['usd_inr']}); verify against Google's pricing page.",
        "",
        "| " + " | ".join(COLUMNS) + " |",
        "|" + "---|" * len(COLUMNS),
    ]
    for r in rows:
        s = r.get("score") or {}
        st = r.get("stability") or {}
        u = r["usage_summary"]
        lines.append(
            "| "
            + " | ".join(
                str(c)
                for c in (
                    r["doc_id"],
                    r["label"],
                    r["status"] + (f" ({r['error']})" if r.get("error") else ""),
                    r.get("readiness") or "-",
                    s.get("must_fix", "-"),
                    s.get("findings", "-"),
                    _pct(s.get("extraction_accuracy")),
                    _pct(s.get("must_find_recall")),
                    len(s.get("violations") or []) if s else "-",
                    f"{st['topic_agreement']} / {st['must_fix_topic_agreement']}"
                    if "topic_agreement" in st
                    else "-",
                    f"{st['checklist_answer_agreement']} / "
                    f"{st['checklist_flagged_jaccard']}"
                    if st.get("checklist_answer_agreement") is not None
                    else "-",
                    f"{u['prompt_tokens']}/{u['output_tokens']}",
                    u["cost_inr"] if u["cost_inr"] is not None else "unpriced",
                    r["latency_s"],
                )
            )
            + " |"
        )
    t = meta["totals"]
    lines += [
        "",
        f"**Totals:** {t['completed']}/{len(rows)} completed; "
        "rejected DPRs with at least one must-fix: "
        f"{t['rejected_flagged']}/{t['rejected']}; "
        f"mean extraction accuracy (annotated): {_pct(t['mean_extraction'])}; "
        f"mean must-find recall (annotated): {_pct(t['mean_recall'])}; "
        f"total cost INR {t['cost_inr']}; mean latency {t['mean_latency_s']} s.",
        "",
        "Annotations marked `reviewed: false` are drafts from a model run, "
        "not ground truth: treat their scores as provisional.",
    ]
    path = out_dir / "summary.md"
    path.write_text("\n".join(lines) + "\n")
    return path


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument(
        "--only", nargs="*", help="doc-id prefixes, or 'approved'/'rejected'"
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--model", help="override DPR_AGENTIC_GEMINI_MODEL")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--record", action="store_true", help="save Gemini responses for --replay"
    )
    mode.add_argument(
        "--replay", action="store_true", help="serve recorded responses; no network"
    )
    parser.add_argument("--draft-annotations", action="store_true")
    parser.add_argument(
        "--pricing-json", type=Path, help="override USD-per-1M-token prices"
    )
    parser.add_argument("--usd-inr", type=float, default=DEFAULT_USD_INR)
    parser.add_argument("--yes", action="store_true", help="confirm paid Gemini calls")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    model = args.model or settings.dpr_agentic_gemini_model
    pricing = (
        json.loads(args.pricing_json.read_text())
        if args.pricing_json
        else DEFAULT_PRICING
    )

    found = discover(args.data_dir)
    docs = select(found, args.only)
    if not docs:
        print(f"No PDFs selected under {args.data_dir}")
        return 1
    runs = len(docs) * args.repeat
    print(f"{len(docs)} documents x {args.repeat} repeat(s) = {runs} runs on {model}")
    for d in docs:
        print(
            f"  {d['id']}  {d['label']:8}  "
            f"annotated={load_annotation(d['id']) is not None}"
        )
    if not args.replay and not args.yes:
        print(
            "\nLive mode makes paid Gemini calls: roughly INR "
            f"{ESTIMATED_INR_PER_RUN * runs:.0f} "
            f"(~INR {ESTIMATED_INR_PER_RUN:.0f}/run, measured per run in the report). "
            "Re-run with --yes to proceed, or --replay to use recordings."
        )
        return 0
    if not args.replay and not settings.dpr_agentic_gemini_api_key:
        print("DPR_AGENTIC_GEMINI_API_KEY is not set")
        return 1

    started = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = EVAL_DIR / "results" / started
    out_dir.mkdir(parents=True, exist_ok=True)
    cassettes = EVAL_DIR / "cassettes"
    transport = (
        ReplayTransport(cassettes)
        if args.replay
        else RecordingTransport(cassettes)
        if args.record
        else None
    )
    http, gemini = build_gemini(settings, model, transport)
    liteparse = LiteParseClient(
        cli_path=settings.dpr_agentic_liteparse_cli_path,
        ocr_language=settings.dpr_agentic_liteparse_ocr_language,
        timeout=settings.dpr_agentic_liteparse_timeout_seconds,
    )
    # Every DPR found, not only the selected ones: a run with --only used to
    # shrink the manifest to the documents it ran.
    manifest = {d["id"]: str(d["path"]) for d in found}
    (EVAL_DIR / "manifest.local.json").write_text(json.dumps(manifest, indent=1))

    rows: list[dict] = []
    try:
        for doc in docs:
            records = []
            for k in range(1, args.repeat + 1):
                print(f"[{doc['id']}] run {k}/{args.repeat} ...", flush=True)
                rec = await run_once(doc, k, gemini, liteparse, settings, out_dir)
                print(
                    f"[{doc['id']}]   {rec['status']} in {rec['latency_s']} s",
                    flush=True,
                )
                records.append(rec)
            first = records[0]
            annotation = load_annotation(doc["id"])
            completed = [r for r in records if r["status"] == "completed"]
            row = {
                "doc_id": doc["id"],
                "label": doc["label"],
                "status": first["status"],
                "error": first.get("error"),
                "readiness": first.get("readiness"),
                "latency_s": round(
                    sum(r["latency_s"] for r in records) / len(records), 1
                ),
                "usage_summary": usage_summary(
                    [u for r in records for u in r["usage"]],
                    model,
                    pricing=pricing,
                    usd_inr=args.usd_inr,
                ),
                "stability": stability([r["result"] for r in completed]),
            }
            if completed:
                score = score_document(
                    doc["id"],
                    doc["label"],
                    completed[0]["result"],
                    annotation,
                    first.get("readiness"),
                )
                row["score"] = {
                    "must_fix": score.must_fix,
                    "findings": score.findings,
                    "reviewed_annotation": score.reviewed,
                    "extraction_accuracy": score.extraction_accuracy,
                    "extraction": [asdict(f) for f in score.extraction],
                    "must_find_recall": score.finding_score.recall
                    if score.finding_score
                    else None,
                    "missed": score.finding_score.missed if score.finding_score else [],
                    "violations": score.finding_score.violations
                    if score.finding_score
                    else [],
                    "label_agrees": score.label_agrees,
                }
                ann_path = EVAL_DIR / "annotations" / f"{doc['id']}.yaml"
                if args.draft_annotations and not ann_path.exists():
                    ann_path.parent.mkdir(parents=True, exist_ok=True)
                    ann_path.write_text(
                        yaml.safe_dump(
                            draft_annotation(
                                doc["sha256"], doc["label"], completed[0]["result"]
                            ),
                            sort_keys=False,
                            allow_unicode=True,
                        )
                    )
            rows.append(row)
    finally:
        await http.aclose()

    scored = [r["score"] for r in rows if r.get("score")]
    ext = [
        s["extraction_accuracy"] for s in scored if s["extraction_accuracy"] is not None
    ]
    rec = [s["must_find_recall"] for s in scored if s["must_find_recall"] is not None]
    rejected = [r for r in rows if r["label"] == "rejected"]
    costs = [
        r["usage_summary"]["cost_inr"]
        for r in rows
        if r["usage_summary"]["cost_inr"] is not None
    ]
    meta = {
        "started": started,
        "model": model,
        "mode": "replay" if args.replay else "record" if args.record else "live",
        "repeat": args.repeat,
        "usd_inr": args.usd_inr,
        "totals": {
            "completed": sum(1 for r in rows if r["status"] == "completed"),
            "rejected": len(rejected),
            "rejected_flagged": sum(
                1 for r in rejected if (r.get("score") or {}).get("must_fix", 0) > 0
            ),
            "mean_extraction": sum(ext) / len(ext) if ext else None,
            "mean_recall": sum(rec) / len(rec) if rec else None,
            "cost_inr": round(sum(costs), 2),
            "mean_latency_s": round(sum(r["latency_s"] for r in rows) / len(rows), 1),
        },
    }
    if isinstance(transport, ReplayTransport):
        meta["cassette"] = {"hits": transport.hits, "misses": transport.misses}
    elif isinstance(transport, RecordingTransport):
        meta["cassette"] = {"recorded": transport.recorded}
    (out_dir / "summary.json").write_text(
        json.dumps({"meta": meta, "documents": rows}, indent=1, default=str)
    )
    report = write_report(out_dir, rows, meta)
    print(f"\n{report.read_text()}\nResults: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
