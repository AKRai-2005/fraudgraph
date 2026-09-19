"""The canonical catalogue of graph queries.

This module is the *contract*.  Both backends implement exactly these names with
exactly these parameters:

* ``fraudgraph.graph.tigergraph.TigerGraphBackend``  -- installed GSQL queries
* ``fraudgraph.graph.local_mirror.LocalMirrorBackend`` -- pandas over the parquet cache

Keeping one catalogue means an investigation is reproducible on either backend
and the evidence ``ref`` string in an answer file (``query:card_window(...)``)
always names something real.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class QuerySpec:
    name: str
    params: tuple[str, ...]
    purpose: str
    gsql_file: str = ""
    optional_params: tuple[str, ...] = field(default_factory=tuple)

    def ref(self, **kwargs) -> str:
        """The human-readable reference string that goes into evidence.ref."""
        inner = ", ".join(f"{k}={v}" for k, v in kwargs.items() if v is not None)
        return f"query:{self.name}({inner})"


CATALOGUE: dict[str, QuerySpec] = {
    q.name: q
    for q in [
        QuerySpec(
            "txn_detail",
            ("txn_id",),
            "One transaction with its card, customer, billing region, email domains "
            "and (for online) its identity/device record.",
            "txn_detail.gsql",
        ),
        QuerySpec(
            "card_window",
            ("card_id", "center_ts"),
            "Transactions on one card inside a time window around a point of "
            "interest. The workhorse for burst, card-testing and structuring "
            "detection.",
            "card_window.gsql",
            ("hours_before", "hours_after", "limit"),
        ),
        QuerySpec(
            "card_profile",
            ("card_id", "before_ts"),
            "The cardholder's established behaviour strictly before a timestamp: "
            "amount distribution, product codes, channels, billing regions, "
            "device profiles and merchant-side email domains. This is the "
            "baseline every 'inconsistent with history' claim is measured against.",
            "card_profile.gsql",
            ("lookback_days",),
        ),
        QuerySpec(
            "card_timeline",
            ("card_id",),
            "Ordered transaction timeline for a card, following the NEXT edge.",
            "card_timeline.gsql",
            ("from_ts", "to_ts", "limit"),
        ),
        QuerySpec(
            "customer_cards",
            ("customer_id",),
            "Every card the customer holds, with activity summary. Needed before "
            "any BLOCK_ALL_CARDS reasoning (policy R10).",
            "customer_cards.gsql",
        ),
        QuerySpec(
            "device_neighbors",
            ("device_profile",),
            "Other cards and customers that transacted from the same device "
            "profile inside a window. The shared-origin signal behind policy R6.",
            "device_neighbors.gsql",
            ("from_ts", "to_ts", "limit"),
        ),
        QuerySpec(
            "region_neighbors",
            ("region_id",),
            "Cards active in a billing region inside a window, used to tell a "
            "region cluster apart from an ordinary busy region.",
            "region_neighbors.gsql",
            ("from_ts", "to_ts", "limit"),
        ),
        QuerySpec(
            "email_neighbors",
            ("domain", "role"),
            "Cards sharing a purchaser or recipient email domain in a window.",
            "email_neighbors.gsql",
            ("from_ts", "to_ts", "limit"),
        ),
        QuerySpec(
            "closed_cases_for_card",
            ("card_id",),
            "Prior investigations on this exact card.",
            "closed_cases_for_card.gsql",
        ),
        QuerySpec(
            "closed_cases_for_customer",
            ("customer_id",),
            "Prior investigations on any of the customer's cards.",
            "closed_cases_for_customer.gsql",
        ),
        QuerySpec(
            "closed_cases_for_device",
            ("device_profile",),
            "Prior investigations that touched this device profile, either "
            "directly or through a connected card.",
            "closed_cases_for_device.gsql",
        ),
        QuerySpec(
            "closed_cases_for_region",
            ("region_id",),
            "Prior investigations whose transactions were billed in this region.",
            "closed_cases_for_region.gsql",
        ),
        QuerySpec(
            "similar_closed_cases",
            ("pattern", "channel"),
            "Case-memory retrieval: closed investigations that resemble the "
            "current alert on pattern, channel, amount band and burst shape.",
            "similar_closed_cases.gsql",
            ("amount", "n_txns", "limit"),
        ),
        QuerySpec(
            "region_history_for_card",
            ("card_id", "region_id", "before_ts"),
            "Whether this card has any history in a billing region before a "
            "timestamp -- the out-of-region test (policy R2/R3).",
            "region_history_for_card.gsql",
        ),
        QuerySpec(
            "device_history_for_card",
            ("card_id", "device_profile", "before_ts"),
            "Whether this card has used this device profile before -- the "
            "new-device test (pattern 3).",
            "device_history_for_card.gsql",
        ),
        QuerySpec(
            "write_case",
            ("case",),
            "Persist an investigation case, its evidence, its actions and its "
            "edges to transactions/cards/devices into the graph.",
            "write_case.gsql",
        ),
        QuerySpec(
            "read_cases",
            (),
            "Read back agent-written cases from the graph (case memory).",
            "read_cases.gsql",
            ("case_id", "limit"),
        ),
    ]
}


def spec(name: str) -> QuerySpec:
    try:
        return CATALOGUE[name]
    except KeyError:  # pragma: no cover - programmer error
        raise KeyError(
            f"unknown graph query {name!r}; known: {sorted(CATALOGUE)}"
        ) from None
