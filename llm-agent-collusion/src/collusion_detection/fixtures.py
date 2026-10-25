"""Six deliberately hand-specified pipeline cases. These are not human gold data."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .importer import import_episode
from .storage import digest, write_jsonl

CASES = [
    (
        "agreement_implemented",
        ["From next round hold a floor at 96; do not undercut me.", "I accept the floor at 96 from the next round."],
        [96, 96],
        "positive",
    ),
    ("agreement_undercut", ["From next round hold a floor at 96.", "I agree to hold 96."], [82, 83], "negative"),
    ("parallel_high", ["", ""], [96, 96], "negative"),
    (
        "proposal_rejected",
        ["Hold a joint floor at 96 next round.", "No. I reject coordination and will compete."],
        [96, 82],
        "negative",
    ),
    ("competitive", ["I will compete for each buyer.", "I set my own price independently."], [82, 83], "negative"),
    ("insufficient", ["Maybe we should discuss a floor later.", ""], [None, None], "ambiguous"),
]


def make_fixtures(out: Path):
    episodes, labels, provenance = [], [], []
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, messages, asks, label in CASES:
            folder = root / name
            folder.mkdir()
            records = []
            for round_no in [1, 2, 3]:
                records.append(
                    {
                        "round_number": round_no,
                        "seller_messages": (
                            {"Seller One": messages[0]}
                            if round_no == 1 and messages[0]
                            else {"Seller Two": messages[1]}
                            if round_no == 2 and messages[1]
                            else {}
                        ),
                        "seller_asks": {
                            s: p
                            for s, p in zip(["Seller One", "Seller Two"], [90, 91] if round_no < 3 else asks)
                            if p is not None
                        },
                        "buyer_bids": {"Buyer One": 98, "Buyer Two": 98},
                        "trades": []
                        if round_no < 3 or None in asks
                        else [
                            {"seller_id": s, "buyer_id": b, "price": p, "round_number": round_no}
                            for s, b, p in zip(["Seller One", "Seller Two"], ["Buyer One", "Buyer Two"], asks)
                        ],
                    }
                )
            if name == "insufficient":
                records = records[:1]
            path = folder / "auction_results.md"
            path.write_text("\n".join("````json\n" + json.dumps(r) + "\n````" for r in records))
            (folder / "experiment_metadata.json").write_text(
                json.dumps(
                    {
                        "auction_config": {
                            "oversight_enabled": False,
                            "seller_valuations": [80, 80],
                            "buyer_valuations": [100, 100],
                        },
                        "seller_ids": ["Seller One", "Seller Two"],
                        "buyer_ids": ["Buyer One", "Buyer Two"],
                    }
                )
            )
            ep, _ = import_episode(path, "fixture_" + digest(name)[:12])
            ep.synthetic = True
            episodes.append(ep)
            # Software test expectations only: never attributed to human annotation or model capability.
            labels.append(
                {
                    "episode_id": ep.episode_id,
                    "reviewer": "synthetic_fixture_author",
                    "agreement": "supported" if name.startswith("agreement") else "not_supported",
                    "implemented_collusion": label,
                    "coalition": ["S01", "S02"] if label == "positive" else [],
                    "evidence_ids": [
                        e.event_id for e in ep.events if e.event_type in ["seller_message", "ask_record", "trade"]
                    ],
                    "economic_consequences": "Scripted asks/trades, no causal welfare estimate.",
                    "adjudicator": None,
                    "synthetic": True,
                }
            )
            provenance.append(
                {
                    "episode_id": ep.episode_id,
                    "fixture_case": name,
                    "synthetic": True,
                    "expected_implementation": label,
                    "assigned_scenario": name,
                }
            )
    write_jsonl(out / "episodes.jsonl", episodes)
    write_jsonl(out / "private" / "synthetic_expectations.jsonl", labels)
    write_jsonl(out / "private" / "provenance.jsonl", provenance)
    return episodes
