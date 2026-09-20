"""The investigation state machine.

Stages follow the lifecycle the challenge asks for: trigger, case creation,
planned evidence gathering, pattern analysis, risk and uncertainty, planning,
controlled evidence request, reassessment, next-best action, termination.

The planner is adaptive: which queries run depends on what the previous ones
returned, and a query is never repeated.  The LLM contributes narrative and
review only -- verdict, probability, pattern, actions, routes and the SAR
decision are all produced by deterministic code.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import pandas as pd

from ..config import RUNTIME
from ..graph.store import GraphStore
from ..schemas import (
    Action, AnswerFile, CaseStatus, Evidence, EvidenceRequest, EvidenceSource,
    InvestigationCase, NextBestActions, Pattern, PatternFinding, SAR,
    TimelineEntry, Verdict,
)
from ..analysis import features as F
from ..analysis import patterns as P
from ..analysis import risk as RISK
from ..policy.engine import DecisionState, PolicyEngine, SharedOrigin, stopping_decision
from ..memory.case_memory import CaseMemory
from .evidence_requests import EvidenceRequestSimulator

MAX_EPISODE_TXNS = 60


@dataclass
class Trigger:
    case_id: str
    trigger_type: str
    trigger_text: str
    flagged_txn_id: str
    card_id: str
    customer_id: str
    opened_at: str = ""
    risk_score: float | None = None

    @classmethod
    def from_case_pack_row(cls, row) -> "Trigger":
        rs = row.get("risk_score")
        return cls(
            case_id=str(row["case_id"]),
            trigger_type=str(row["trigger_type"]),
            trigger_text=str(row["trigger_text"]),
            flagged_txn_id=str(int(row["flagged_txn_id"])),
            card_id=str(row["card_id"]),
            customer_id=str(row["customer_id"]),
            opened_at=str(row.get("opened_at") or ""),
            risk_score=float(rs) if rs is not None and str(rs) not in ("", "nan") else None,
        )


@dataclass
class InvestigationState:
    trigger: Trigger
    investigation_id: str
    ctx: F.CaseContext
    timeline: list[TimelineEntry] = field(default_factory=list)
    step: int = 0
    evidence: list[Evidence] = field(default_factory=list)
    findings: list[PatternFinding] = field(default_factory=list)
    episodes: dict = field(default_factory=dict)
    stop_reason: str = ""
    # Optional observer, called once per timeline entry as it happens. The
    # console uses it to stream an investigation while it runs; it must never
    # be able to change the outcome, so its exceptions are swallowed.
    on_step: Callable[[TimelineEntry], None] | None = None

    def log(self, kind: str, detail: str, **data) -> None:
        self.step += 1
        entry = TimelineEntry(step=self.step, kind=kind, detail=detail, data=data)
        self.timeline.append(entry)
        if self.on_step is not None:
            try:
                self.on_step(entry)
            except Exception:  # noqa: BLE001 - an observer cannot break a case
                pass


class InvestigationAgent:
    """Runs one investigation end to end."""

    def __init__(
        self,
        store: GraphStore | None = None,
        memory: CaseMemory | None = None,
        narrator=None,
        simulator: EvidenceRequestSimulator | None = None,
        on_step: Callable[[TimelineEntry], None] | None = None,
    ):
        self.store = store or GraphStore()
        self.memory = memory or CaseMemory(self.store)
        self.policy = PolicyEngine()
        self.simulator = simulator or EvidenceRequestSimulator()
        self.narrator = narrator  # optional LLM narrator; None -> template writer
        self.on_step = on_step

    # ------------------------------------------------------------------ run
    def investigate(self, trigger: Trigger) -> AnswerFile:
        t0 = time.perf_counter()
        # tokens_used on the narrator is cumulative across the whole run, so the
        # per-case figure is the delta. Summing the cumulative value over 20
        # cases produced a triangular number roughly ten times the truth.
        tokens_at_start = getattr(self.narrator, "tokens_used", 0) if self.narrator else 0
        self.store.reset()
        inv_id = f"INV-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:8]}"
        ctx = F.CaseContext(case_id=trigger.case_id, trigger=trigger.__dict__)
        st = InvestigationState(trigger=trigger, investigation_id=inv_id, ctx=ctx,
                                on_step=self.on_step)

        st.log("trigger", f"{trigger.trigger_type} alert opened: {trigger.trigger_text}",
               investigation_id=inv_id, flagged_txn_id=trigger.flagged_txn_id,
               card_id=trigger.card_id, opened_at=trigger.opened_at)

        # ---- stage 3: evidence gathering (planned) ----
        self._gather_core(st)
        if not ctx.txn.get("found"):
            return self._abort_unresolvable(st, t0)
        self._gather_targeted(st)
        self._gather_agent_planned(st)

        # ---- stage 4: pattern analysis ----
        feats = F.compute(ctx)
        st.findings, st.episodes = P.run_all(ctx, feats)
        matched = [f for f in st.findings if f.matched]
        st.log("finding",
               f"{len(matched)} of {len(st.findings)} detectors matched: "
               + (", ".join(f"{f.name} ({f.strength:.2f})" for f in matched) or "none"),
               detectors=[f.name for f in matched])

        # ---- stage 5: risk and uncertainty ----
        # A customer_report trigger *is* a cardholder denial: it is evidence in
        # hand from step one, not something the agent has to go and request.
        disputes = trigger.trigger_type == "customer_report"
        risk = RISK.assess(st.findings, feats, customer_disputes=disputes,
                           trigger_type=trigger.trigger_type)
        st.log("decision",
               f"fraud probability {risk.agent_fraud_probability:.2f} from "
               f"{risk.independent_signal_count} independent signals "
               f"(bank model said {feats.bank_risk_score:.2f}); confidence {risk.confidence:.2f}",
               probability=risk.agent_fraud_probability, confidence=risk.confidence)

        # ---- memory ----
        similar = self.memory.retrieve_similar(ctx, feats, st.findings)
        st.log("tool", f"case memory returned {len(similar)} comparable closed investigations",
               case_ids=[c["case_id"] for c in similar])

        # ---- episode, exposure, connections ----
        episode = self._select_episode(st, feats, risk)
        shared = self._shared_origin(st, feats)

        self._build_evidence(st, feats, risk, similar, shared, episode)

        # ---- stage 6/9: initial next-best action ----
        state = self._decision_state(
            st, feats, risk, episode, shared, phase="initial",
            customer_response=True if disputes else None, asked=False,
        )
        initial = self.policy.evaluate(state)
        st.log("policy",
               "initial recommendation: "
               + ", ".join(f"{a.action.value}[{a.route.value}]" for a in initial.actions),
               rules=initial.rules_cited, blocked=initial.blocked_actions)

        # ---- stage 7/8: controlled evidence request and reassessment ----
        requests: list[EvidenceRequest] = []
        final = initial
        final_risk = risk
        what_changed = "nothing"
        needs_evidence = any(
            a.action in (Action.VERIFY_WITH_CUSTOMER, Action.STEP_UP_AUTH)
            for a in initial.actions
        )
        if needs_evidence:
            req = self.simulator.request(
                kind="customer_validation",
                after_step=st.step,
                features=feats,
                risk=risk,
                findings=st.findings,
                trigger=trigger,
            )
            requests.append(req)
            st.log("evidence_request",
                   f"requested {req.type}: {req.reason}",
                   assumed_response=req.assumed_response, status=req.status)
            answered = self.simulator.denied_flag(req)
            final_risk = RISK.reassess_with_customer_response(
                risk, answered, st.findings, already_counted=disputes
            )
            episode = self._select_episode(st, feats, final_risk)
            state = self._decision_state(
                st, feats, final_risk, episode, shared, phase="final",
                customer_response=answered, asked=True,
            )
            final = self.policy.evaluate(state)
            req.effect_on_investigation = (
                f"probability moved {risk.agent_fraud_probability:.2f} -> "
                f"{final_risk.agent_fraud_probability:.2f}"
            )
            what_changed = self._what_changed(initial, final, risk, final_risk, req)
            st.log("policy",
                   "final recommendation: "
                   + ", ".join(f"{a.action.value}[{a.route.value}]" for a in final.actions),
                   rules=final.rules_cited)

        # ---- stage 10: termination ----
        should_stop, reason = stopping_decision(
            probability=final_risk.agent_fraud_probability,
            independent_signals=final_risk.independent_signal_count,
            customer_answered=(
                disputes
                or (bool(requests) and self.simulator.denied_flag(requests[0]) is not None)
            ),
            steps_used=st.step,
            max_steps=RUNTIME.max_investigation_steps,
            new_evidence_last_step=bool(requests),
        )
        st.stop_reason = reason or "Investigation reached a defensible decision."
        st.log("stop", st.stop_reason)

        # ---- assemble ----
        answer = self._assemble(
            st, feats, final_risk, initial, final, requests, similar, shared,
            episode, what_changed, risk, t0, tokens_at_start,
        )
        return answer

    # ------------------------------------------------------- evidence stages
    def _gather_core(self, st: InvestigationState) -> None:
        ctx, tr = st.ctx, st.trigger
        ctx.txn = self.store.call("txn_detail", txn_id=tr.flagged_txn_id)
        if not ctx.txn.get("found"):
            st.log("tool", f"flagged transaction {tr.flagged_txn_id} not found in the graph")
            return
        ctx.card_id = ctx.txn.get("card_id") or tr.card_id
        ctx.customer_id = ctx.txn.get("customer_id") or tr.customer_id
        if ctx.card_id != tr.card_id:
            st.log("tool",
                   f"card on the flagged transaction ({ctx.card_id}) differs from the alert "
                   f"({tr.card_id}); using the transaction's card")
        ts = ctx.ts
        ctx.profile = self.store.call("card_profile", card_id=ctx.card_id, before_ts=ts)
        ctx.window = self.store.call(
            "card_window", card_id=ctx.card_id, center_ts=ts, hours_before=72, hours_after=72
        )
        ctx.wide_window = self.store.call(
            "card_window", card_id=ctx.card_id, center_ts=ts,
            hours_before=24 * 180, hours_after=24 * 30, limit=4000
        )
        st.log("tool",
               f"baseline established: {ctx.profile.get('n_txns', 0)} prior transactions on "
               f"{ctx.card_id}; {len((ctx.window or {}).get('transactions') or [])} in the "
               f"+/-72h window")

    def _gather_targeted(self, st: InvestigationState) -> None:
        """Only ask the graph what this particular alert makes relevant."""
        ctx = st.ctx
        t, ts = ctx.txn, ctx.ts
        plan: list[str] = []

        if t.get("addr1") is not None:
            ctx.region_test = self.store.call(
                "region_history_for_card", card_id=ctx.card_id,
                region_id=t["addr1"], before_ts=ts,
            )
            plan.append("region history")
            if int(ctx.region_test.get("prior_txns_in_region") or 0) == 0:
                lo = str(pd.Timestamp(ts) - pd.Timedelta(days=14))
                hi = str(pd.Timestamp(ts) + pd.Timedelta(days=14))
                ctx.region_cluster = self.store.call(
                    "region_neighbors", region_id=t["addr1"], from_ts=lo, to_ts=hi
                )
                plan.append("region cluster (region is new to this card)")

        if t.get("device_profile"):
            ctx.device_test = self.store.call(
                "device_history_for_card", card_id=ctx.card_id,
                device_profile=t["device_profile"], before_ts=ts,
            )
            plan.append("device history")
            if int(ctx.device_test.get("prior_txns_on_device") or 0) == 0 or \
                    t.get("id_23") in ("IP_PROXY:ANONYMOUS", "IP_PROXY:HIDDEN"):
                lo = str(pd.Timestamp(ts) - pd.Timedelta(days=P.RING_WINDOW_DAYS))
                hi = str(pd.Timestamp(ts) + pd.Timedelta(days=P.RING_WINDOW_DAYS))
                ctx.device_ring = self.store.call(
                    "device_neighbors", device_profile=t["device_profile"],
                    from_ts=lo, to_ts=hi,
                )
                plan.append("device neighbours (device new to this card or proxied)")
                if int(ctx.device_ring.get("n_cards") or 0) >= P.RING_MIN_CARDS:
                    ctx.prior_cases_device = self.store.call(
                        "closed_cases_for_device", device_profile=t["device_profile"]
                    )
                    plan.append("closed cases on the shared device")

        ctx.prior_cases_card = self.store.call("closed_cases_for_card", card_id=ctx.card_id)
        ctx.prior_cases_customer = self.store.call(
            "closed_cases_for_customer", customer_id=ctx.customer_id
        )
        ctx.customer_cards = self.store.call("customer_cards", customer_id=ctx.customer_id)
        plan += ["prior cases on card", "prior cases on customer", "customer's other cards"]
        st.log("tool", "targeted retrieval: " + "; ".join(plan))

    def _gather_agent_planned(self, st: InvestigationState) -> None:
        """Let the model propose *additional* retrieval on top of the baseline.

        Proposals are validated against the query catalogue before execution, so
        the model can widen an investigation but cannot invent a tool, skip the
        mandatory baseline, or reach anything the deterministic planner could not.
        """
        if self.narrator is None or not getattr(self.narrator, "enabled", False):
            return
        ctx = st.ctx
        t = ctx.txn
        # Ask the model only when the deterministic plan has not turned up a
        # lead. If the baseline already found a device ring, a burst, or a prior
        # case on this card, there is plenty to reason over and a further query
        # would be confirmation rather than discovery. This also keeps a
        # free-tier daily request allowance for the narratives, which matter
        # more: the planner added no query on any of the 20 exam cases.
        ring = int((ctx.device_ring or {}).get("n_cards") or 0)
        window = len((ctx.window or {}).get("transactions") or [])
        priors = (len((ctx.prior_cases_card or {}).get("direct") or [])
                  + int((ctx.prior_cases_device or {}).get("n") or 0))
        if ring >= P.RING_MIN_CARDS or window >= 3 or priors:
            st.log("tool",
                   "agent planner not consulted: the baseline already produced a lead "
                   f"(device ring {ring} cards, {window} transactions in the window, "
                   f"{priors} prior case(s) on this card or device)")
            return
        summary = "\n".join([
            f"Flagged transaction {t.get('TransactionID')} on card {ctx.card_id} "
            f"(customer {ctx.customer_id}): ${float(t.get('TransactionAmt') or 0):,.2f} "
            f"{t.get('channel')} on {ctx.ts}, product {t.get('ProductCD')}, "
            f"billing region {t.get('addr1')}, bank risk score {t.get('risk_score')}.",
            f"Device profile: {t.get('device_profile') or 'none (no identity record)'}; "
            f"marked {t.get('id_15')}; proxy {t.get('id_23') or 'none'}.",
            f"Card history before the alert: {(ctx.profile or {}).get('n_txns', 0)} transactions.",
            f"Prior closed cases on this card: "
            f"{len((ctx.prior_cases_card or {}).get('direct') or [])}; on this customer: "
            f"{(ctx.prior_cases_customer or {}).get('n', 0)}.",
            f"Device neighbours in window: "
            f"{(ctx.device_ring or {}).get('n_cards', 'not queried')}.",
        ])
        already = [f"{c.name}:{sorted(c.params.items())}" for c in self.store.ledger]
        try:
            plan = self.narrator.plan_extra_queries(summary, already, max_new=3)
        except Exception:  # noqa: BLE001
            return
        if not plan:
            st.log("tool", "agent planner: baseline retrieval judged sufficient; no extra queries")
            return
        for step in plan:
            result = self.store.call(step["name"], **step["params"])
            st.log("tool",
                   f"agent-planned query {step['name']}({step['params']}): {step['why']}",
                   planned_by="llm", ok=not bool(result.get("error")))
            self._absorb(st, step["name"], step["params"], result)

    def _absorb(self, st: InvestigationState, name: str, params: dict, result: dict) -> None:
        """File an agent-planned result into the context if it fills a gap."""
        ctx = st.ctx
        if result.get("error"):
            return
        slot = {
            "device_neighbors": "device_ring",
            "region_neighbors": "region_cluster",
            "closed_cases_for_device": "prior_cases_device",
            "closed_cases_for_region": "prior_cases_region",
            "region_history_for_card": "region_test",
            "device_history_for_card": "device_test",
            "customer_cards": "customer_cards",
        }.get(name)
        if slot and hasattr(ctx, slot) and not getattr(ctx, slot):
            setattr(ctx, slot, result)

    # ------------------------------------------------------------- analysis
    def _select_episode(self, st, feats: F.Features, risk) -> P.Episode:
        """Pick the fraud episode from the detector that actually fired.

        A legitimate verdict has no episode: the README requires
        ``affected_txn_ids`` empty and ``exposure_usd`` 0 for that case.
        """
        if risk.agent_fraud_probability < 0.5:
            return P.Episode([], "", 0.0)
        ranked = sorted(
            (f for f in st.findings if f.matched and f.name in st.episodes),
            key=lambda f: -f.strength,
        )
        if not ranked:
            flagged = st.ctx.txn
            return P.Episode(
                [str(flagged.get("TransactionID"))], str(flagged.get("TransactionID")),
                round(abs(feats.amount), 2), str(flagged.get("ts"))[:10], str(flagged.get("ts"))[:10],
            )
        ep = st.episodes[ranked[0].name]
        flagged_id = str(st.ctx.txn.get("TransactionID"))
        if flagged_id not in ep.txn_ids:
            # the flagged transaction is always part of the episode it triggered
            rows = (st.ctx.window or {}).get("transactions") or []
            extra = [r for r in rows if str(r["TransactionID"]) == flagged_id]
            ep = P._episode(
                [r for r in rows if str(r["TransactionID"]) in set(ep.txn_ids)] + extra
            )
        if len(ep.txn_ids) > MAX_EPISODE_TXNS:
            ep.txn_ids = ep.txn_ids[:MAX_EPISODE_TXNS]
        return ep

    def _shared_origin(self, st, feats: F.Features) -> SharedOrigin | None:
        dr = st.ctx.device_ring or {}
        cards = [c["card_id"] for c in (dr.get("cards") or []) if c.get("card_id") != feats.card_id]
        if feats.device_profile and len(cards) >= 2 and feats.ring_new_fraction >= 0.9 \
                and feats.anonymous_proxy:
            known = (st.ctx.prior_cases_device or {}).get("cases") or []
            return SharedOrigin(
                kind="device_profile", value=feats.device_profile, card_ids=cards,
                cards_with_known_fraud=sum(
                    1 for c in known if c.get("outcome") == "confirmed_fraud"
                ),
            )
        return None

    # -------------------------------------------------------------- evidence
    def _build_evidence(self, st, feats, risk, similar, shared, episode) -> None:
        ctx = st.ctx
        add = st.evidence.append
        be = self.store.backend_name

        add(Evidence(
            claim=(
                f"Flagged transaction {feats.txn_id} on card {feats.card_id}: ${feats.amount:,.2f} "
                f"{feats.channel.replace('_', '-')} under product code {feats.product_cd} on "
                f"{feats.ts}, scored {feats.bank_risk_score:.2f} by the bank's model."
            ),
            source=EvidenceSource.GRAPH,
            ref=self.store.ref("txn_detail", txn_id=feats.txn_id),
            entity_ids=[feats.txn_id, feats.card_id], backend=be,
        ))
        if feats.hist_n_txns:
            add(Evidence(
                claim=(
                    f"The card's established behaviour before this alert: {feats.hist_n_txns} "
                    f"transactions, median ${feats.hist_median_amt:,.2f}, 95th percentile "
                    f"${feats.hist_p95_amt:,.2f}, maximum ${feats.hist_max_amt:,.2f}; "
                    f"{feats.channel} accounts for {feats.channel_share:.0%} of them."
                ),
                source=EvidenceSource.GRAPH,
                ref=self.store.ref("card_profile", card_id=feats.card_id, before_ts=feats.ts),
                entity_ids=[feats.card_id], backend=be,
            ))
        for f in st.findings:
            if not f.matched:
                continue
            add(Evidence(
                claim=f"{f.name.replace('_', ' ').capitalize()}: {f.why}.",
                source=EvidenceSource.GRAPH,
                ref=self._ref_for_finding(f, feats),
                entity_ids=(f.txn_ids or f.entity_ids)[:25],
                confidence=f.strength, backend=be, weight_tag=f.name,
            ))
        if shared:
            add(Evidence(
                claim=(
                    f"Shared origin: device profile '{shared.value}' appears on "
                    f"{len(shared.card_ids) + 1} distinct cards inside the alert window"
                    + (f", and {shared.cards_with_known_fraud} of them already carry a confirmed-fraud "
                       "closed case" if shared.cards_with_known_fraud else "")
                    + "."
                ),
                source=EvidenceSource.GRAPH,
                ref=self.store.ref("device_neighbors", device_profile=shared.value),
                entity_ids=[shared.value] + shared.card_ids[:25], backend=be,
            ))
        for c in similar[:4]:
            add(Evidence(
                claim=(
                    f"Case memory: closed case {c['case_id']} ({c['outcome']}, pattern "
                    f"{c['pattern']}, exposure ${float(c['exposure_usd']):,.2f}) matches this alert "
                    f"on {c.get('why_retrieved', 'pattern and shape')}."
                ),
                source=EvidenceSource.GRAPH,
                ref=self.store.ref("similar_closed_cases", pattern=c.get("pattern"),
                                   channel=feats.channel),
                entity_ids=[c["case_id"]], backend=be,
            ))
        if risk.uncertainty_notes:
            add(Evidence(
                claim="Remaining uncertainty: " + "; ".join(risk.uncertainty_notes) + ".",
                source=EvidenceSource.GRAPH,
                ref="risk:uncertainty_assessment",
                entity_ids=[feats.card_id], backend=be,
            ))
        for fail in self.store.failures():
            add(Evidence(
                claim=(
                    f"Evidence gap: the {fail.name} query failed ({fail.error}); this is recorded "
                    "as a gap and is not read as evidence for or against fraud."
                ),
                source=EvidenceSource.GRAPH, ref=fail.ref, entity_ids=[], backend=be,
            ))

    def _ref_for_finding(self, f: PatternFinding, feats: F.Features) -> str:
        if f.name == "shared_device_ring":
            return self.store.ref("device_neighbors", device_profile=feats.device_profile)
        if f.name == "out_of_region_use":
            return self.store.ref("region_history_for_card", card_id=feats.card_id,
                                  region_id=feats.region_id, before_ts=feats.ts)
        if f.name == "cnp_new_device":
            return self.store.ref("device_history_for_card", card_id=feats.card_id,
                                  device_profile=feats.device_profile, before_ts=feats.ts)
        if f.name in ("recurring_charge", "consistent_with_history"):
            return self.store.ref("card_profile", card_id=feats.card_id, before_ts=feats.ts)
        return self.store.ref("card_window", card_id=feats.card_id, center_ts=feats.ts)

    # ---------------------------------------------------------- policy input
    def _decision_state(
        self, st, feats, risk, episode, shared, phase: str,
        customer_response: bool | None = None, asked: bool = False,
    ) -> DecisionState:
        tr = st.trigger
        undocumented = [
            f for f in st.findings
            if f.matched and f.pattern is Pattern.UNDOCUMENTED
        ]
        ct = next((f for f in st.findings if f.name == "card_testing" and f.matched), None)
        rc = next((f for f in st.findings if f.name == "recurring_charge" and f.matched), None)
        cleared_over_100 = False
        if ct:
            rows = (st.ctx.window or {}).get("transactions") or []
            ids = set(ct.txn_ids)
            cleared_over_100 = any(
                float(r["TransactionAmt"]) > 100.0 for r in rows if str(r["TransactionID"]) in ids
            )
        p = risk.agent_fraud_probability
        verdict = self._verdict(p, risk)
        cust_fraud_cards = sum(
            1 for c in ((st.ctx.prior_cases_customer or {}).get("cases") or [])
            if c.get("outcome") == "confirmed_fraud"
        )
        return DecisionState(
            phase=phase,
            fraud_probability=p,
            verdict=verdict,
            exposure=episode.exposure,
            independent_signal_count=risk.independent_signal_count,
            conflicting_evidence=bool(risk.conflicting_evidence),
            pattern=self._pattern(st, risk, feats),
            pattern_is_undocumented=bool(undocumented),
            coordinated_across_customers=bool(shared and shared.describes_ring),
            trigger_type=tr.trigger_type,
            customer_disputes_charge=tr.trigger_type == "customer_report",
            customer_response_denied=customer_response,
            asked_customer=asked,
            card_testing=bool(ct),
            cleared_purchase_over_100=cleared_over_100,
            recurring_charge_dispute=bool(rc),
            shared_origin=shared,
            customer_n_cards=feats.customer_n_cards,
            customer_cards_with_confirmed_fraud=cust_fraud_cards,
            credentials_confirmed_compromised=False,
            has_pending_authorisation=True,
        )

    @staticmethod
    def _verdict(p: float, risk) -> Verdict:
        if risk.conflicting_evidence and 0.25 < p < 0.75:
            return Verdict.UNCERTAIN
        if p >= 0.70:
            return Verdict.FRAUD
        if p <= 0.30:
            return Verdict.LEGITIMATE
        return Verdict.UNCERTAIN

    def _pattern(self, st, risk, feats=None) -> Pattern:
        """The typology, from the detector that fired -- or from the shape of the
        transaction when a fraud verdict rests on the cardholder's denial alone.

        Most confirmed fraud in this dataset leaves no graph signature: it is a
        single ordinary-looking transaction the cardholder later disputed.
        Returning ``none`` there would contradict the verdict, so the fallback
        mirrors how the shipped history labels exactly those cases. Measured on
        the 4,665 confirmed frauds, the first fraudulent transaction's channel
        determines the label almost perfectly:

          in_person -> out_of_region_use (955) when the billing region is new to
                       the card, otherwise account_takeover (1,117)
          online    -> card_not_present_new_device (1,076) when the identity
                       record marks the device New, otherwise
                       card_not_present_fraud (1,404)

        No other pattern appears on an in-person first transaction, and no
        card-not-present label appears on one.
        """
        if risk.agent_fraud_probability < 0.5:
            return Pattern.NONE
        matched = [f for f in st.findings if f.matched and f.pattern is not Pattern.NONE]
        if matched:
            return max(matched, key=lambda f: f.strength).pattern
        if feats is None:
            return Pattern.NONE
        if feats.channel == "in_person":
            return Pattern.OUT_OF_REGION_USE if feats.region_novel else Pattern.ACCOUNT_TAKEOVER
        if feats.channel == "online":
            return (Pattern.CARD_NOT_PRESENT_NEW_DEVICE
                    if (feats.device_marked_new and feats.device_novel)
                    else Pattern.CARD_NOT_PRESENT_FRAUD)
        return Pattern.NONE

    def _pattern_finding(self, st, risk) -> PatternFinding | None:
        if risk.agent_fraud_probability < 0.5:
            return None
        matched = [f for f in st.findings if f.matched and f.pattern is not Pattern.NONE]
        return max(matched, key=lambda f: f.strength) if matched else None

    # --------------------------------------------------------------- output
    @staticmethod
    def _what_changed(initial, final, risk0, risk1, req: EvidenceRequest) -> str:
        a0 = {a.action for a in initial.actions}
        a1 = {a.action for a in final.actions}
        added = [a.action.value for a in final.actions if a.action not in a0]
        removed = [a.action.value for a in initial.actions if a.action not in a1]
        if not added and not removed and abs(
            risk0.agent_fraud_probability - risk1.agent_fraud_probability
        ) < 0.02:
            return "nothing"
        bits = [
            f"The simulated {req.type.replace('_', ' ')} response ({req.assumed_response}) moved "
            f"the assessed probability from {risk0.agent_fraud_probability:.2f} to "
            f"{risk1.agent_fraud_probability:.2f}."
        ]
        if added:
            bits.append(f"Added {', '.join(added)}.")
        if removed:
            bits.append(f"Withdrew {', '.join(removed)}.")
        return " ".join(bits)

    def _assemble(
        self, st, feats, risk, initial, final, requests, similar, shared, episode,
        what_changed, risk0, t0, tokens_at_start: int = 0,
    ) -> AnswerFile:
        pattern = self._pattern(st, risk, feats)
        pf = self._pattern_finding(st, risk)
        verdict = self._verdict(risk.agent_fraud_probability, risk)
        actions = final.actions
        status = self._status(verdict, actions, requests, risk.agent_fraud_probability)

        connected_cards = sorted(set(shared.card_ids))[:40] if shared else []
        connected_devices = [shared.value] if shared else (
            [feats.device_profile] if feats.device_profile and feats.ring_cards >= P.RING_MIN_CARDS
            else []
        )
        if verdict is Verdict.LEGITIMATE:
            episode = P.Episode([], "", 0.0)
            connected_cards, connected_devices = [], []

        case = InvestigationCase(
            status=status,
            verdict=verdict,
            fraud_probability=risk.agent_fraud_probability,
            pattern=pattern,
            pattern_description=(pf.description if pf and pattern is Pattern.UNDOCUMENTED else ""),
            affected_txn_ids=episode.txn_ids,
            first_suspicious_txn_id=episode.first_txn_id,
            connected_card_ids=connected_cards,
            connected_device_profiles=connected_devices,
            exposure_usd=episode.exposure,
            evidence=st.evidence,
            similar_prior_cases=[c["case_id"] for c in similar[:6]],
            summary="",
        )
        sar = self._build_sar(st, feats, risk, final, episode, shared, case)
        answer = AnswerFile(
            case_id=st.trigger.case_id,
            case=case,
            evidence_requests=requests,
            next_best_actions=NextBestActions(
                initial=initial.actions, final=final.actions, what_changed=what_changed
            ),
            sar=sar,
            stop_reason=st.stop_reason,
            tool_calls=self.store.call_count,
            tokens=0,
            latency_s=round(time.perf_counter() - t0, 2),
            findings=st.findings,
            risk=risk,
            timeline=st.timeline,
            tool_log=list(self.store.ledger),
            trigger=st.trigger.__dict__,
        )
        # narrative last, so it can only describe what was already decided
        from .narrative import write_summary, write_sar_narrative

        case.summary = write_summary(answer, feats, risk, self.narrator)
        if sar.file:
            # Populate the structured fields first: the narrative quotes the
            # activity dates, so writing it before they are set would put the
            # flagged transaction's own date on both ends of the range.
            sar.subjects = sorted({
                feats.customer_id, feats.card_id, *connected_cards[:10],
                *( [feats.device_profile] if feats.device_profile else [] ),
            })
            sar.total_amount_usd = episode.exposure
            sar.activity_dates = [episode.first_ts, episode.last_ts] if episode.first_ts else []
            sar.narrative = write_sar_narrative(answer, feats, risk, shared, self.narrator)
        if self.narrator is not None:
            answer.tokens = max(0, getattr(self.narrator, "tokens_used", 0) - tokens_at_start)

        # Persist to case memory. written_to_graph is set from what the graph
        # actually accepted -- never assumed.
        persisted = self.memory.write_case(answer)
        case.written_to_graph = persisted["written"]
        case.graph_case_id = persisted["graph_case_id"]
        st.log(
            "tool",
            f"case memory write: {'stored in TigerGraph as ' + persisted['graph_case_id'] if persisted['written'] else 'not written to the graph (' + str(persisted['detail'].get('reason') or persisted['detail'].get('error') or 'backend unavailable') + ')'}",
            written=persisted["written"], backend=persisted["backend"],
        )
        answer.tool_calls = self.store.call_count
        answer.timeline = st.timeline
        answer.tool_log = list(self.store.ledger)
        return answer

    @staticmethod
    def _status(verdict: Verdict, actions, requests, probability: float) -> CaseStatus:
        """Where the case stands when the agent stops.

        A concluded verdict closes the case even when an analyst hand-off is
        also recommended (R6/R9 escalate a confirmed ring for human review);
        ``escalated`` is reserved for cases whose verdict a human must settle.
        """
        acts = {a.action for a in actions}
        if verdict is Verdict.FRAUD and probability >= 0.70:
            return CaseStatus.CLOSED_FRAUD
        if verdict is Verdict.LEGITIMATE and probability <= 0.30:
            return CaseStatus.CLOSED_LEGITIMATE
        if Action.ESCALATE_TO_ANALYST in acts:
            return CaseStatus.ESCALATED
        return CaseStatus.OPEN

    def _build_sar(self, st, feats, risk, decision, episode, shared, case) -> SAR:
        file_it = decision.sar_required and any(
            a.action is Action.FILE_REPORT for a in decision.actions
        )
        return SAR(file=file_it, reason=decision.sar_reason)

    def _abort_unresolvable(self, st, t0) -> AnswerFile:
        """The flagged transaction does not exist. Fail safe, do not guess."""
        st.stop_reason = (
            f"Flagged transaction {st.trigger.flagged_txn_id} could not be resolved in the graph, "
            "so no evidence-based verdict is possible. Escalated to a human analyst rather than "
            "assuming an outcome."
        )
        st.log("stop", st.stop_reason)
        from ..schemas import ActionRecommendation, Route

        esc = [ActionRecommendation(
            action=Action.ESCALATE_TO_ANALYST, route=Route.AUTO,
            reason="R8: the alert cannot be evaluated because its transaction is not in the graph",
        )]
        case = InvestigationCase(
            status=CaseStatus.ESCALATED, verdict=Verdict.UNCERTAIN, fraud_probability=0.5,
            pattern=Pattern.NONE,
            evidence=[Evidence(
                claim=f"Flagged transaction {st.trigger.flagged_txn_id} was not found in the graph.",
                source=EvidenceSource.GRAPH,
                ref=self.store.ref("txn_detail", txn_id=st.trigger.flagged_txn_id),
                entity_ids=[st.trigger.flagged_txn_id],
            )],
            summary="The flagged transaction could not be resolved, so the alert was escalated "
                    "without a verdict.",
        )
        return AnswerFile(
            case_id=st.trigger.case_id, case=case,
            next_best_actions=NextBestActions(initial=esc, final=esc, what_changed="nothing"),
            sar=SAR(file=False, reason="3a: no evidence of fraud was established."),
            stop_reason=st.stop_reason, tool_calls=self.store.call_count,
            latency_s=round(time.perf_counter() - t0, 2),
            timeline=st.timeline, tool_log=list(self.store.ledger), trigger=st.trigger.__dict__,
        )
