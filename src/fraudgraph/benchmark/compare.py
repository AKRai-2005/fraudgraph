"""Compare two runs of the benchmark field by field.

The three backends are supposed to be interchangeable: the catalogue is the
contract, so an investigation run against TigerGraph, against the official
`tigergraph-mcp` server, or against the local pandas mirror should reach the
same conclusion *from the same evidence*.

"Same conclusion" was the only thing ever checked, and it hid two real bugs:

* ``card_profile`` reported a 95th percentile of $424.99 on TigerGraph and
  $425.08 on the local mirror, because pandas interpolates quantiles and the
  GSQL path took the nearest observed amount;
* case memory cited **different prior cases** -- CC-2935 against CC-1589 for
  the same alert -- because ties in the retrieval score were resolved by
  whatever order the engine happened to return its rows in.

Both changed the evidence in a published answer file while leaving the verdict,
the probability and the exposure identical, so every summary-level check passed.
This module compares the whole answer, so that cannot happen again.

Three fields are *expected* to differ and are excluded by name, each for a
stated reason -- see ``EXPECTED_TO_DIFFER``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# Field paths (relative to the answer root, with list indices stripped) that
# may legitimately differ between two runs.  Anything not listed here is a bug.
EXPECTED_TO_DIFFER: dict[str, str] = {
    "latency_s": "wall-clock time; a network round trip is not a local dict lookup",
    "tokens": "LLM token counts vary per call and are 0 when the narrator is off",
    "case.written_to_graph": (
        "a real capability difference: the local mirror is read-only and says so, "
        "rather than claiming a write it did not make"
    ),
    "case.graph_case_id": "only a backend that actually persisted the case has one",
}


@dataclass
class Difference:
    case_id: str
    path: str
    left: object
    right: object

    def __str__(self) -> str:
        return (f"  {self.case_id}.{self.path}\n"
                f"      {str(self.left)[:160]}\n"
                f"      {str(self.right)[:160]}")


@dataclass
class ComparisonResult:
    left_name: str
    right_name: str
    cases_compared: int = 0
    missing: list[str] = field(default_factory=list)
    differences: list[Difference] = field(default_factory=list)
    ignored: list[Difference] = field(default_factory=list)

    @property
    def agree(self) -> bool:
        return not self.differences and not self.missing

    def report(self) -> str:
        lines = [
            f"Comparing {self.cases_compared} answer files: "
            f"{self.left_name} vs {self.right_name}",
        ]
        if self.missing:
            lines.append(f"\nMISSING from one side ({len(self.missing)}): "
                         + ", ".join(self.missing))
        if self.differences:
            lines.append(f"\nUNEXPECTED DIFFERENCES ({len(self.differences)}):")
            lines.extend(str(d) for d in self.differences[:60])
            if len(self.differences) > 60:
                lines.append(f"  ... and {len(self.differences) - 60} more")
        by_path: dict[str, int] = {}
        for d in self.ignored:
            by_path[_strip_indices(d.path)] = by_path.get(_strip_indices(d.path), 0) + 1
        if by_path:
            lines.append("\nExpected differences, by field:")
            for p, n in sorted(by_path.items(), key=lambda kv: -kv[1]):
                lines.append(f"  {n:>3}x {p}  -- {EXPECTED_TO_DIFFER.get(p, '')}")
        lines.append("\nAGREE" if self.agree
                     else f"\nDISAGREE on {len(self.differences)} field(s)")
        return "\n".join(lines)


def _strip_indices(path: str) -> str:
    out, depth = [], 0
    for ch in path:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            out.append("[]")
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _walk(left, right, path: str, case_id: str, res: ComparisonResult) -> None:
    bucket = (res.ignored if _strip_indices(path).lstrip(".") in EXPECTED_TO_DIFFER
              else res.differences)
    if type(left) is not type(right) and not (
        isinstance(left, (int, float)) and isinstance(right, (int, float))
    ):
        bucket.append(Difference(case_id, path.lstrip("."), left, right))
        return
    if isinstance(left, dict):
        for key in sorted(set(left) | set(right)):
            _walk(left.get(key), right.get(key), f"{path}.{key}", case_id, res)
    elif isinstance(left, list):
        if len(left) != len(right):
            bucket.append(Difference(
                case_id, path.lstrip("."),
                f"{len(left)} items", f"{len(right)} items"))
            return
        for i, (a, b) in enumerate(zip(left, right)):
            _walk(a, b, f"{path}[{i}]", case_id, res)
    elif left != right:
        bucket.append(Difference(case_id, path.lstrip("."), left, right))


def compare_answers(left: dict, right: dict, case_id: str,
                    res: ComparisonResult) -> None:
    """Compare one pair of answer dicts, accumulating into ``res``."""
    _walk(left, right, "", case_id, res)
    res.cases_compared += 1


def compare_dirs(left_dir: Path, right_dir: Path,
                 left_name: str = "", right_name: str = "") -> ComparisonResult:
    """Compare two directories of ``HHG-0NN.json`` answer files."""
    left_dir, right_dir = Path(left_dir), Path(right_dir)
    res = ComparisonResult(left_name or left_dir.name, right_name or right_dir.name)
    left_files = {p.stem: p for p in left_dir.glob("HHG-*.json")}
    right_files = {p.stem: p for p in right_dir.glob("HHG-*.json")}
    for stem in sorted(set(left_files) ^ set(right_files)):
        res.missing.append(stem)
    for stem in sorted(set(left_files) & set(right_files)):
        compare_answers(
            json.loads(left_files[stem].read_text(encoding="utf-8")),
            json.loads(right_files[stem].read_text(encoding="utf-8")),
            stem, res,
        )
    return res
