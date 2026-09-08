"""B2: Logistic regression p_fill GTX entry, validation theo thoi gian.

Features: spread_bps, atr_bps, hour_vn, symbol (one-hot).
Train: 70% dau theo thoi gian. Test: 30% sau.
Khong dung random split - thi truong co regime drift.

Output: JSON ra stdout.
CAUTION: uoc luong de uu tien phien live, KHONG thay the fill that.
"""
from __future__ import annotations
import json, math, sqlite3, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
F4 = {"conv_min": 0.3575, "atr_lo": 6.0, "atr_hi": 20.0, "spread_min": 2.0}
WINDOW = 60.0


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


def build_fill_map(journal):
    fill_map = {}
    for kind, ts, p in journal:
        if kind != "ORDER_STATE":
            continue
        cid = p.get("client_id", "")
        if not cid:
            continue
        if cid not in fill_map:
            fill_map[cid] = {"filled": 0.0, "status": ""}
        filled = float(p.get("filled", 0))
        if filled > fill_map[cid]["filled"]:
            fill_map[cid]["filled"] = filled
        fill_map[cid]["status"] = p.get("status", fill_map[cid]["status"])
    return fill_map


def classify_entry(cid, fill_map):
    fm = fill_map.get(cid, {})
    if fm.get("filled", 0) > 0:
        return 1  # filled
    status = fm.get("status", "")
    if status in ("EXPIRED", "EXPIRED_IN_MATCH", "CANCELED", "REJECTED"):
        return 0  # unfilled
    return None  # unknown


def sigmoid(x):
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    ex = math.exp(x)
    return ex / (1.0 + ex)


def logistic_fit(X, y, lr=0.05, epochs=500, l2=0.01):
    """Gradient descent logistic regression. X: list of feature dicts, y: list of 0/1."""
    # Collect all feature names
    feat_names = sorted({k for row in X for k in row})
    n = len(X)
    # Convert to matrix
    Xm = [[row.get(f, 0.0) for f in feat_names] for row in X]
    d = len(feat_names)
    w = [0.0] * d
    b = 0.0
    for epoch in range(epochs):
        grad_w = [0.0] * d
        grad_b = 0.0
        loss = 0.0
        for xi, yi in zip(Xm, y):
            z = sum(w[j] * xi[j] for j in range(d)) + b
            p = sigmoid(z)
            err = p - yi
            for j in range(d):
                grad_w[j] += err * xi[j]
            grad_b += err
            loss += -(yi * math.log(max(p, 1e-15)) + (1 - yi) * math.log(max(1 - p, 1e-15)))
        for j in range(d):
            w[j] = w[j] * (1 - lr * l2) - lr * grad_w[j] / n
        b -= lr * grad_b / n
    return w, b, feat_names


def predict(X, w, b, feat_names):
    preds = []
    for row in X:
        xi = [row.get(f, 0.0) for f in feat_names]
        z = sum(w[j] * xi[j] for j in range(len(feat_names))) + b
        preds.append(sigmoid(z))
    return preds


def brier_score(probs, labels):
    return sum((p - y) ** 2 for p, y in zip(probs, labels)) / len(probs)


def log_loss(probs, labels):
    return -sum(
        y * math.log(max(p, 1e-15)) + (1 - y) * math.log(max(1 - p, 1e-15))
        for p, y in zip(probs, labels)
    ) / len(probs)


def auc_roc(probs, labels):
    """Wilcoxon-Mann-Whitney AUC."""
    pos = [p for p, y in zip(probs, labels) if y == 1]
    neg = [p for p, y in zip(probs, labels) if y == 0]
    if not pos or not neg:
        return None
    concordant = sum(1 for p in pos for n in neg if p > n)
    tied = sum(1 for p in pos for n in neg if p == n)
    total = len(pos) * len(neg)
    return (concordant + 0.5 * tied) / total


def main():
    journal = load_journal()
    probes = load_probes()
    fill_map = build_fill_map(journal)

    # Build probe index
    probe_index = defaultdict(list)
    for pr in probes:
        probe_index[(pr["symbol"], pr.get("side", ""))].append((pr["opened"], pr))
    for key in probe_index:
        probe_index[key].sort()

    # Build dataset
    entries = [(ts, p) for kind, ts, p in journal if kind == "ENTRY_INTENT"]
    all_symbols = sorted({p.get("symbol", "") for _, p in entries})

    dataset = []
    for ts, p in entries:
        cid = p.get("client_id", "")
        symbol = p.get("symbol", "")
        side = p.get("side", "")
        label = classify_entry(cid, fill_map)
        if label is None:
            continue  # skip UNKNOWN

        # Find closest probe
        candidates = probe_index.get((symbol, side), [])
        best_probe = None
        best_gap = float("inf")
        for opened, pr in candidates:
            gap = abs(opened - ts)
            if gap <= WINDOW and gap < best_gap:
                best_gap = gap
                best_probe = pr

        if best_probe is None:
            # No probe match: use defaults
            spread_bps = 0.0
            atr_bps = 0.0
        else:
            spread_bps = best_probe.get("spread_bps", 0.0)
            atr_bps = best_probe.get("atr_bps", 0.0)

        import datetime
        hour_vn = (datetime.datetime.utcfromtimestamp(ts) +
                   datetime.timedelta(hours=7)).hour

        # Features
        feat = {
            "spread_bps": spread_bps,
            "atr_bps": atr_bps,
            "hour_sin": math.sin(2 * math.pi * hour_vn / 24),
            "hour_cos": math.cos(2 * math.pi * hour_vn / 24),
        }
        # Symbol one-hot (drop first for identifiability)
        for sym in all_symbols[1:]:
            feat[f"sym_{sym}"] = 1.0 if symbol == sym else 0.0

        dataset.append({"ts": ts, "feat": feat, "label": label, "symbol": symbol,
                        "spread_bps": spread_bps, "atr_bps": atr_bps, "hour_vn": hour_vn})

    # Sort by time, split 70/30
    dataset.sort(key=lambda r: r["ts"])
    split = int(len(dataset) * 0.70)
    train = dataset[:split]
    test = dataset[split:]

    X_train = [r["feat"] for r in train]
    y_train = [r["label"] for r in train]
    X_test = [r["feat"] for r in test]
    y_test = [r["label"] for r in test]

    # Baseline: predict mean fill rate
    baseline_p = sum(y_train) / len(y_train) if y_train else 0.5
    baseline_preds_test = [baseline_p] * len(y_test)

    # Fit logistic
    w, b, feat_names = logistic_fit(X_train, y_train, lr=0.05, epochs=800, l2=0.01)
    train_preds = predict(X_train, w, b, feat_names)
    test_preds = predict(X_test, w, b, feat_names)

    # Metrics
    train_brier = brier_score(train_preds, y_train)
    test_brier = brier_score(test_preds, y_test)
    baseline_brier = brier_score(baseline_preds_test, y_test)
    test_auc = auc_roc(test_preds, y_test)
    test_logloss = log_loss(test_preds, y_test)

    # Coefficients
    coefs = dict(zip(feat_names, w))
    coefs["intercept"] = b

    # Predicted p_fill for F4 vs non-F4 in test set
    f4_test = [r for r in test if (
        r["spread_bps"] >= F4["spread_min"] and
        F4["atr_lo"] <= r["atr_bps"] < F4["atr_hi"]
    )]
    non_f4_test = [r for r in test if not (
        r["spread_bps"] >= F4["spread_min"] and
        F4["atr_lo"] <= r["atr_bps"] < F4["atr_hi"]
    )]

    def mean_pred(group):
        if not group:
            return None
        preds = predict([r["feat"] for r in group], w, b, feat_names)
        return round(sum(preds) / len(preds), 4)

    output = {
        "ghichu": (
            "B2: logistic p_fill, train=70% dau theo thoi gian, test=30% sau. "
            "CAUTION: uoc luong de uu tien phien live, KHONG thay the fill that. "
            "n_unknown bi loai khoi dataset."
        ),
        "dataset": {
            "total_known": len(dataset),
            "train_n": len(train),
            "test_n": len(test),
            "train_fill_rate": round(sum(y_train) / len(y_train), 4) if y_train else None,
            "test_fill_rate": round(sum(y_test) / len(y_test), 4) if y_test else None,
        },
        "metrics": {
            "train_brier": round(train_brier, 5),
            "test_brier": round(test_brier, 5),
            "baseline_brier_test": round(baseline_brier, 5),
            "brier_skill_score": round(1 - test_brier / baseline_brier, 4) if baseline_brier else None,
            "test_auc_roc": round(test_auc, 4) if test_auc else None,
            "test_log_loss": round(test_logloss, 5),
        },
        "coefficients": {k: round(v, 5) for k, v in sorted(coefs.items())},
        "predicted_p_fill": {
            "f4_test_mean": mean_pred(f4_test),
            "non_f4_test_mean": mean_pred(non_f4_test),
            "f4_test_n": len(f4_test),
            "non_f4_test_n": len(non_f4_test),
        },
        "interpretation": (
            "Brier skill score > 0 = model tot hon baseline. "
            "AUC > 0.5 = co kha nang phan biet filled vs unfilled. "
            "Coefficients: spread_bps am = spread cao -> kho fill hon. "
            "atr_bps am = ATR cao -> kho fill hon."
        ),
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
