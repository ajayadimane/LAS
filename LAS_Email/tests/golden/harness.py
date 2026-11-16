"""Golden-set runner and metrics (per-intent and per-field precision/recall + outcome checks)."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select

from las.common.deps import Deps
from las.common.fakes import FakeDocumentParser, FakeLLM
from las.common.interfaces import LLMRequest, LLMResponse
from las.common.models import AiUsage, CoreIntakeHandoff, InboundEmail, MailAction
from tests.docs import pdf, png, xlsx
from tests.helpers import deliver, make_mail

TRACKED = ("name", "mobile", "email", "services")
TOP_TRACKED = (
    "services",
    "menu_choices",
    "txn_ref",
    "amount",
    "attachment_filenames",
    "confirm_answer",
)


@dataclass
class CaseResult:
    case_id: str
    predicted: list[dict[str, Any]]
    outcome_failures: list[str] = field(default_factory=list)
    tokens: int = 0


def _norm(v: Any) -> Any:
    if isinstance(v, list):
        return sorted(v, key=str)
    if isinstance(v, str):
        return v.strip()
    return v


def flatten(entities: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for i, c in enumerate(entities.get("customers") or []):
        for k in TRACKED:
            out[f"customers.{i}.{k}"] = c.get(k)
    for k in TOP_TRACKED:
        out[k] = entities.get(k)
    corr = entities.get("correction") or {}
    out["correction.new"] = corr.get("new")
    return out


def _present(v: Any) -> bool:
    return v not in (None, "", [])


def _general(path: str) -> str:
    parts = path.split(".")
    return ".".join("*" if p.isdigit() else p for p in parts)


# --- execution -------------------------------------------------------------------------------------
def _attachments(deps: Deps, specs: list[dict[str, Any]]) -> list[tuple[str, str, bytes]]:
    out = []
    for a in specs:
        if a["kind"] == "pdf":
            out.append((a["filename"], "application/pdf", pdf(a["text"])))
        elif a["kind"] == "png":
            data = png(a["filename"].encode())
            ocr = deps.parser
            if isinstance(ocr, FakeDocumentParser):
                ocr.texts[hashlib.sha256(data).hexdigest()] = a["text"]
            out.append((a["filename"], "image/png", data))
        elif a["kind"] == "xlsx":
            out.append(
                (
                    a["filename"],
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    xlsx(a["rows"]),
                )
            )
    return out


def _install_replay(deps: Deps, recorded: Any, doc_class: dict[str, Any] | None) -> None:
    queue = list(recorded) if isinstance(recorded, list) else [recorded]
    llm = deps.llm_client
    assert isinstance(llm, FakeLLM)

    def handler(req: LLMRequest) -> LLMResponse:
        if "You classify a single document" in req.system:
            body = doc_class or {"doc_class": "OTHER", "confidence": 0.5}
        elif "You check whether a document" in req.system:
            # doc_relevance: no golden case exercises a rejection here — the deterministic layer
            # (type marker + customer name in text) is what each case's fixture text is written
            # to satisfy; this LLM pass always says plausible so it never interferes.
            body = {"plausible": True, "reason": ""}
        else:
            body = queue.pop(0) if len(queue) > 1 else queue[0]
        return LLMResponse(
            text=json.dumps(body), input_tokens=900, output_tokens=150, stop_reason="STOP"
        )

    llm.queue.clear()
    llm.handler = handler


async def run_case(deps: Deps, case: dict[str, Any], *, live: bool) -> CaseResult:
    """Deliver the case's mail (after any earlier mails in its thread) and collect what the
    extraction produced. This service executes nothing -- Core LAS does -- so the outcome checks
    are: the mail is queued for Core exactly once, and escalation happened where expected. The
    cases' older outcome keys (executed, replies, creates, ...) describe the retired standalone
    workflow and are not checked."""
    for prior in case.get("prior", []):
        if not live:
            _install_replay(deps, prior["recorded"], None)
        await deliver(deps, make_mail(subject=prior.get("subject", "Request"), body=prior["body"]))
    if not live:
        _install_replay(deps, case["recorded"], case.get("doc_class"))
    mail = case["mail"]
    async with deps.db() as s:
        last_usage = (
            await s.execute(select(func.coalesce(func.max(AiUsage.usage_id), 0)))
        ).scalar_one()
    ib = await deliver(
        deps,
        make_mail(
            subject=mail["subject"],
            body=mail["body"],
            attachments=_attachments(deps, mail.get("attachments", [])),
        ),
    )
    async with deps.db() as s:
        actions = list(
            (
                await s.execute(
                    select(MailAction)
                    .where(MailAction.inbound_id == ib.inbound_id)
                    .order_by(MailAction.seq)
                )
            ).scalars()
        )
        tokens = (
            await s.execute(
                select(
                    func.coalesce(func.sum(AiUsage.input_tokens + AiUsage.output_tokens), 0)
                ).where(AiUsage.usage_id > last_usage)
            )
        ).scalar_one()
        inbound = (
            await s.execute(select(InboundEmail).where(InboundEmail.inbound_id == ib.inbound_id))
        ).scalar_one()
        handoffs = (
            await s.execute(
                select(func.count())
                .select_from(CoreIntakeHandoff)
                .where(CoreIntakeHandoff.inbound_id == ib.inbound_id)
            )
        ).scalar_one()
    exp = case["expected"]
    res = CaseResult(
        case["id"],
        [{"intent": a.intent, "entities": a.entities, "status": a.status} for a in actions],
        tokens=int(tokens),
    )
    fail = res.outcome_failures.append
    if inbound.processing_status != "PROCESSED" or handoffs != 1:
        fail(f"inbound {inbound.processing_status}, {handoffs} handoff(s) to Core (expected 1)")
    if exp.get("escalated") and inbound.parse_method != "LLM_ESCALATED":
        fail("expected escalation")
    return res


# --- metrics ---------------------------------------------------------------------------------------
@dataclass
class PR:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 1.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 1.0


def score(
    cases: list[dict[str, Any]], results: list[CaseResult]
) -> tuple[dict[str, PR], dict[str, PR]]:
    intents: dict[str, PR] = defaultdict(PR)
    fields: dict[str, PR] = defaultdict(PR)
    for case, res in zip(cases, results, strict=True):
        expected = case["expected"]["actions"]
        exp_c = Counter(e["intent"] for e in expected)
        pred_c = Counter(p["intent"] for p in res.predicted)
        for intent in set(exp_c) | set(pred_c):
            tp = min(exp_c[intent], pred_c[intent])
            intents[intent].tp += tp
            intents[intent].fp += pred_c[intent] - tp
            intents[intent].fn += exp_c[intent] - tp
        by_intent: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for p in res.predicted:
            by_intent[p["intent"]].append(p)
        for e in expected:
            pool = by_intent.get(e["intent"]) or []
            pred = flatten(pool.pop(0)["entities"]) if pool else {}
            for path, want in e["fields"].items():
                key = _general(path)
                got = pred.get(path)
                want_n, got_n = _norm(want), _norm(got)
                if not _present(want):
                    if _present(got):
                        fields[key].fp += 1
                    else:
                        fields[key].tp += 1
                elif got_n == want_n:
                    fields[key].tp += 1
                else:
                    fields[key].fn += 1
                    if _present(got):
                        fields[key].fp += 1
    return dict(intents), dict(fields)


def report(
    mode: str,
    cases: list[dict[str, Any]],
    results: list[CaseResult],
    intents: dict[str, PR],
    fields: dict[str, PR],
) -> str:
    lines = [
        f"# Golden set report ({mode} mode)",
        "",
        f"Cases: {len(cases)} · outcome checks passed: "
        f"{sum(not r.outcome_failures for r in results)}/{len(results)} · "
        f"tokens: {sum(r.tokens for r in results)}",
        "",
        "## Per intent",
        "",
        "| intent | precision | recall | TP | FP | FN |",
        "|---|---|---|---|---|---|",
    ]
    for k in sorted(intents):
        m = intents[k]
        lines.append(f"| {k} | {m.precision:.3f} | {m.recall:.3f} | {m.tp} | {m.fp} | {m.fn} |")
    lines += [
        "",
        "## Per field",
        "",
        "| field | precision | recall | TP | FP | FN |",
        "|---|---|---|---|---|---|",
    ]
    for k in sorted(fields):
        m = fields[k]
        lines.append(f"| {k} | {m.precision:.3f} | {m.recall:.3f} | {m.tp} | {m.fp} | {m.fn} |")
    failed = [r for r in results if r.outcome_failures]
    lines += ["", "## Outcome failures", ""] + (
        [f"- **{r.case_id}**: {'; '.join(r.outcome_failures)}" for r in failed] or ["none"]
    )
    return "\n".join(lines) + "\n"
