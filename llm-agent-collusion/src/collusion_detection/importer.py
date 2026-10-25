"""Import frozen auction results, explicitly excluding private agent transcripts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .schemas import Episode, Event, Participant
from .storage import digest, write_jsonl

FENCE = re.compile(r"^(`{3,})json[^\n]*\n(.*?)\n\1\s*$", re.MULTILINE | re.DOTALL)


def parse_rounds(path: Path):
    text = path.read_text(encoding="utf-8")
    blocks = FENCE.findall(text)
    if not blocks:
        raise ValueError("No fenced JSON auction results found")
    rounds = [json.loads(body) for _, body in blocks]
    numbers = [r["round_number"] for r in rounds]
    if len(numbers) != len(set(numbers)) or any(not isinstance(n, int) or n < 1 for n in numbers):
        raise ValueError("Duplicate or invalid round number")
    return sorted(rounds, key=lambda r: r["round_number"])


def import_episode(path: Path, group_id: str | None = None):
    path = Path(path)
    rounds = parse_rounds(path)
    meta_path = path.parent / "experiment_metadata.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    config = meta.get("auction_config", {})
    oversight = (
        "enabled"
        if config.get("oversight_enabled") is True
        else "disabled"
        if config.get("oversight_enabled") is False
        else "unknown"
    )
    if (path.parent / "Overseer.md").exists() or any(r.get("overseer_visible_messages") is not None for r in rounds):
        oversight = "enabled"
    sellers = set(meta.get("seller_ids", []))
    buyers = set(meta.get("buyer_ids", []))
    for r in rounds:
        sellers.update(r.get("seller_messages", {}))
        sellers.update(r.get("seller_asks", {}))
        buyers.update(r.get("buyer_bids", {}))
        for trade in r.get("trades", []):
            sellers.add(trade["seller_id"])
            buyers.add(trade["buyer_id"])
    mapping = {name: f"S{i + 1:02}" for i, name in enumerate(sorted(sellers))}
    mapping.update({name: f"B{i + 1:02}" for i, name in enumerate(sorted(buyers))})
    valuations = {}
    for role in ("seller", "buyer"):
        ids, values = meta.get(role + "_ids", []), config.get(role + "_valuations", [])
        if isinstance(values, str):
            values = json.loads(values)
        if len(ids) == len(values):
            valuations.update(dict(zip(ids, values)))
    participants = [
        Participant(alias=alias, role="seller" if name in sellers else "buyer", valuation=valuations.get(name))
        for name, alias in sorted(mapping.items())
    ]
    # Raw paths and metadata never enter the canonical model-facing event representation.
    episode_id = "ep_" + digest(rounds)[:20]
    events = []

    def anonymize(text):
        for name in sorted(mapping, key=len, reverse=True):
            text = text.replace(name, mapping[name])
        tails = {}
        for name, alias in mapping.items():
            tail = name.split(" ", 1)[-1]
            tails.setdefault(tail, []).append(alias)
        for tail, aliases in tails.items():
            if len(aliases) == 1:
                text = re.sub(r"\b" + re.escape(tail) + r"\b", aliases[0], text)
        return text

    def add(r, typ, actor, text, source, observation, available=None):
        events.append(
            Event(
                event_id=f"e{len(events) + 1:05}",
                round=r,
                event_type=typ,
                actor=actor,
                text=anonymize(text),
                source_ref=source,
                observation=observation,
                available_round=available,
            )
        )

    for r in rounds:
        number = r["round_number"]
        for name, message in sorted(r.get("seller_messages", {}).items()):
            if not isinstance(message, str):
                raise TypeError("Seller message must be text")
            if not message:
                continue
            add(
                number,
                "seller_message",
                mapping[name],
                message,
                f"round/{number}/seller_messages/{mapping[name]}",
                "message",
                number + 1,
            )
        for typ, names, key in [("ask_record", sellers, "seller_asks"), ("bid_record", buyers, "buyer_bids")]:
            orders = r.get(key, {})
            for name in sorted(names):
                value = orders.get(name)
                if value is not None and value != "null":
                    price = float(value)
                    text = f"{mapping[name]} recorded {'ask' if typ == 'ask_record' else 'bid'}: {price:g}. "
                    text += "Round bookkeeping; not proof of a price change or persistent queue state."
                    obs = "recorded"
                else:
                    text = f"{mapping[name]} has no numeric {key} entry. "
                    text += "Missing/withdrawn/omitted cannot be distinguished from this log; prior queue order may persist."
                    obs = "missing_or_withdrawn"
                add(number, typ, mapping[name], text, f"round/{number}/{key}/{mapping[name]}", obs)
        for i, trade in enumerate(r.get("trades", [])):
            price = float(trade["price"])
            add(
                number,
                "trade",
                mapping[trade["seller_id"]],
                f"{mapping[trade['buyer_id']]} bought from {mapping[trade['seller_id']]} at {price:g}. "
                "Executed trade; list position does not establish message/action causal order.",
                f"round/{number}/trades/{i}",
                "executed",
            )
        prices = [float(t["price"]) for t in r.get("trades", [])]
        summary = f"Executed trades: {len(prices)}; total paid: {sum(prices):g}."
        if prices:
            summary += f" Mean trade price: {sum(prices) / len(prices):g}."
        trades = r.get("trades", [])
        if trades and all(valuations.get(t["seller_id"]) is not None for t in trades):
            profits = sum(float(t["price"]) - float(valuations[t["seller_id"]]) for t in trades)
            summary += f" Seller profit total on executed trades: {profits:g}."
        if trades and all(valuations.get(t["buyer_id"]) is not None for t in trades):
            surplus = sum(float(valuations[t["buyer_id"]]) - float(t["price"]) for t in trades)
            summary += f" Buyer surplus total on executed trades: {surplus:g}."
        add(number, "outcome", None, summary, f"round/{number}/derived/trade_summary", "derived")
    # Conservative default: all variants in one source directory share a split family.
    family = group_id or meta.get("group_id") or ("grp_" + digest(str(path.parent.parent.resolve()))[:16])
    episode = Episode(
        episode_id=episode_id,
        group_id=family,
        participants=participants,
        rounds=max(r["round_number"] for r in rounds),
        events=events,
        oversight=oversight,
    )
    provenance = {
        "episode_id": episode_id,
        "source_path": str(path.resolve()),
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "alias_map": mapping,
        "original_metadata": meta,
        "oversight": oversight,
        "grouping": "Explicit group map or conservatively shared source-root family; review before pilot splitting.",
    }
    return episode, provenance


def import_market(source: Path, out: Path, include_interventions=False, groups=None, limit=None):
    source, out = Path(source), Path(out)
    paths = [source] if source.is_file() else sorted(source.rglob("auction_results.md"))
    episodes, private, exclusions = [], [], []
    seen = set()
    for path in paths:
        if limit is not None and len(episodes) >= limit:
            break
        ep, provenance = import_episode(path, (groups or {}).get(str(path.parent.name)))
        if ep.episode_id in seen:
            exclusions.append({"source": str(path), "reason": "duplicate frozen episode"})
            continue
        seen.add(ep.episode_id)
        if ep.oversight != "disabled" and not include_interventions:
            exclusions.append({"episode_id": ep.episode_id, "source": str(path), "reason": "oversight " + ep.oversight})
            continue
        episodes.append(ep)
        private.append(provenance)
    write_jsonl(out / "episodes.jsonl", episodes)
    write_jsonl(out / "private" / "provenance.jsonl", private)
    write_jsonl(out / "private" / "import_exclusions.jsonl", exclusions)
    return episodes, exclusions
