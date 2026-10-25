"""Private human-reviewed sampling summary; never uses detector verdicts as gold."""

from collections import Counter
from pathlib import Path


def summarize_sampling(generation, provenance, labels):
    by_source = {str(Path(r["source_path"]).parent.resolve()): r["episode_id"] for r in provenance}
    truth = {r["episode_id"]: r for r in labels}
    if len(truth) != len(labels):
        raise ValueError("Experiment 0 requires one independently resolved label per episode")
    counts = {}
    rows = []
    for sample in generation:
        arm = sample["scenario"]
        eid = by_source.get(str(Path(sample["source_path"]).resolve()))
        label = truth.get(eid, {})
        implemented = label.get("implemented_collusion") or "unreviewed"
        disclosed = label.get("public_disclosure") or "unreviewed"
        counter = counts.setdefault(
            arm,
            {
                "sampled": 0,
                "generation_status": Counter(),
                "implemented_collusion": Counter(),
                "public_disclosure": Counter(),
            },
        )
        counter["sampled"] += 1
        counter["generation_status"][sample["status"]] += 1
        counter["implemented_collusion"][implemented] += 1
        counter["public_disclosure"][disclosed] += 1
        rows.append(
            {
                "sample_id": sample["sample_id"],
                "episode_id": eid,
                "arm": arm,
                "status": sample["status"],
                "implemented_collusion": implemented,
                "public_disclosure": disclosed,
            }
        )
    return {
        "arms": counts,
        "samples": rows,
        "limitations": "Assigned pact is not ground truth; outcomes and public disclosure require independent human "
        "review. All sampled completions and failures are retained. Missing labels are unreviewed.",
    }
