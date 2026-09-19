"""Run one case end to end and print the answer file."""
import json, sys
import pandas as pd
from fraudgraph.config import PATHS
from fraudgraph.agent.orchestrator import InvestigationAgent, Trigger

case_id = sys.argv[1] if len(sys.argv) > 1 else "HHG-014"
cp = pd.read_csv(PATHS.case_pack_csv)
row = cp[cp.case_id == case_id].iloc[0].to_dict()
agent = InvestigationAgent()
ans = agent.investigate(Trigger.from_case_pack_row(row))
print(json.dumps(ans.to_answer_dict(), indent=2)[:6000])
print("\n--- TIMELINE ---")
for t in ans.timeline:
    print(f"  [{t.step:2d}] {t.kind:18s} {t.detail[:150]}")
