"""B1: Tach ENTRY_INTENT thanh F4-qualified va non-F4, bao fill rate rieng.

Join: ENTRY_INTENT (symbol, side, timestamp) <-> probe (symbol, side, opened) trong +/-60s.
F4: conviction>=0.3575 AND 6<=atr_bps<20 AND spread_bps>=2.

Output: JSON ra stdout, khong ghi file data.
"""
from __future__ import annotations
import json, math, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
F4 = {"conv_min": 0.3575, "atr_lo": 6.0, "atr_hi": 20.0, "spread_min": 2.0}
WINDOW = 60.0  # seconds


def wilson(k, n, z=1.96):
    if n == 0:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - h) / d, (c + h) / d


def load_journal():
    db = ROOT / "data" / "testnet-journal.sqlite"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute(
        "select kind, timestamp, payload from events order by timestamp"
    ).fetchall()
    con.close()
    return [(k, ts, json.loads(p)) for k, ts, p in rows]


def load_probes():
    path = ROOT / "data" / "testnet-signal-lab-v2.jsonl"
    probes = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    probes.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return [p for p in probes if p.get("outcome")]


def is_f4(probe):
    return (
        probe["conviction"] >= F4["conv_min"]
        and F4["atr_lo"] <= probe["atr_bps"] < F4["atr_hi"]
        and probe["spread_bps"] >= F4["spread_min"]
    )


def build_fill_map(journal):
    """client_id -> {filled, status}"""
    fill_map = {}
    for kind, ts, p in journal:
        if kind != "ORDER_STATE":
            continue
        cid = p.get("client_id", "")
        if not cid:
            continue
        if cid not in fill_map:
            fill_map[cid] = {"filled": 0.0, "status": "", "ts": ts}
        filled = float(p.get("filled", 0))
        if filled > fill_map[cid]["filled"]:
            fill_map[cid]["filled"] = filled
        fill_map[cid]["status"] = p.get("status", fill_map[cid]["status"])
        fill_map[cid]["ts"] = max(fill_map[cid]["ts"], ts)
    return fill_map


def classify_entry(cid, fill_map):
    """Return 'FILLED', 'UNFILLED', or 'UNKNOWN'."""
    fm = fill_map.get(cid, {})
    if fm.get("filled", 0) > 0:
        return "FILLED"
    status = fm.get("status", "")
    if status in ("EXPIRED", "EXPIRED_IN_MATCH", "CANCELED", "REJECTED"):
        return "UNFILLED"
    return "UNKNOWN"


def main():
    journal = load_journal()
    probes = load_probes()
    fill_map = build_fill_map(journal)

    # Build probe lookup: (symbol, side) -> sorted list of (opened, probe)
    from collections import defaultdict
    probe_index = defaultdict(list)
    for pr in probes:
        probe_index[(pr["symbol"], pr.get("side", ""))].append((pr["opened"], pr))
    for key in probe_index:
        probe_index[key].sort()

    # Process each ENTRY_INTENT
    entries = [(ts, p) for kind, ts, p in journal if kind == "ENTRY_INTENT"]

    results = []
    unmatched = 0
    for ts, p in entries:
        cid = p.get("client_id", "")
        symbol = p.get("symbol", "")
        side = p.get("side", "")
        outcome = classify_entry(cid, fill_map)

        # Find closest probe within WINDOW seconds
        candidates = probe_index.get((symbol, side), [])
        best_probe = None
        best_gap = float("inf")
        for opened, pr in candidates:
            gap = abs(opened - ts)
            if gap <= WINDOW and gap < best_gap:
                best_gap = gap
                best_probe = pr

        f4_qualified = is_f4(best_probe) if best_probe else None
        if best_probe is None:
            unmatched += 1

        results.append({
            "client_id": cid,
            "symbol": symbol,
            "side": side,
            "ts": ts,
            "outcome": outcome,
            "f4_qualified": f4_qualified,
            "probe_gap_s": round(best_gap, 2) if best_probe else None,
            "probe_conviction": best_probe["conviction"] if best_probe else None,
            "probe_atr_bps": best_probe["atr_bps"] if best_probe else None,
            "probe_spread_bps": best_probe["spread_bps"] if best_probe else None,
        })

    # Compute fill rates
    def fill_rate_group(group):
        filled = sum(1 for r in group if r["outcome"] == "FILLED")
        unfilled = sum(1 for r in group if r["outcome"] == "UNFILLED")
        unknown = sum(1 for r in group if r["outcome"] == "UNKNOWN")
        total = filled + unfilled + unknown
        lo, hi = wilson(filled, total)
        return {
            "n": total,
            "filled": filled,
            "unfilled": unfilled,
            "unknown": unknown,
            "fill_rate": round(filled / total, 4) if total else None,
            "wilson95": [round(lo, 4), round(hi, 4)] if lo is not None else None,
        }

    all_entries = results
    f4_entries = [r for r in results if r["f4_qualified"] is True]
    non_f4_entries = [r for r in results if r["f4_qualified"] is False]
    unmatched_entries = [r for r in results if r["f4_qualified"] is None]

    # Per-symbol breakdown
    from collections import defaultdict
    by_symbol = defaultdict(list)
    for r in results:
        by_symbol[r["symbol"]].append(r)

    output = {
        "ghichu": (
            "B1: join ENTRY_INTENT <-> probe (symbol, side, +-60s). "
            "F4: conv>=0.3575 AND 6<=atr<20 AND spread>=2. "
            "fill_rate = filled/(filled+unfilled+unknown). "
            "UNKNOWN = ORDER_STATE chua co hoac status khong ro rang."
        ),
        "all": fill_rate_group(all_entries),
        "f4_qualified": fill_rate_group(f4_entries),
        "non_f4": fill_rate_group(non_f4_entries),
        "unmatched_to_probe": len(unmatched_entries),
        "by_symbol": {
            sym: fill_rate_group(rows)
            for sym, rows in sorted(by_symbol.items())
        },
        "f4_by_symbol": {
            sym: fill_rate_group([r for r in rows if r["f4_qualified"] is True])
            for sym, rows in sorted(by_symbol.items())
            if any(r["f4_qualified"] is True for r in rows)
        },
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
