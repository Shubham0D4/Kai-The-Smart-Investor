"""Analyse data/backtest/mechanical_*.csv from backtest_mechanical.py (no network).
Rank correlation (Spearman) of each signal with 63d excess return vs SPY, pooled and per as-of date
(cross-sectional), plus excess return by MoS bucket and Buy-gate reachability at candidate thresholds."""
import csv, glob, statistics as st, sys
from collections import defaultdict

d = sys.argv[1] if len(sys.argv) > 1 else "data/backtest"
rows = [r for f in glob.glob(f"{d}/mechanical_*.csv") for r in csv.DictReader(open(f))]
F = lambda r, k: float(r[k]) if r[k] not in ("", None) else None
data = [r for r in rows if F(r, "excess_return") is not None]

def rank(v):
    o = sorted(range(len(v)), key=lambda i: v[i]); rk = [0.0] * len(v); i = 0
    while i < len(o):
        j = i
        while j + 1 < len(o) and v[o[j + 1]] == v[o[i]]: j += 1
        for k in range(i, j + 1): rk[o[k]] = (i + j) / 2
        i = j + 1
    return rk

def spearman(x, y):
    if len(x) < 8: return None
    a, b = rank(x), rank(y); ma, mb = st.mean(a), st.mean(b)
    num = sum((p - ma) * (q - mb) for p, q in zip(a, b))
    den = (sum((p - ma) ** 2 for p in a) * sum((q - mb) ** 2 for q in b)) ** .5
    return num / den if den else None

print(f"rows {len(rows)}, with outcome {len(data)}, with DCF {sum(1 for r in data if r['mos'])}, tickers {len({r['ticker'] for r in rows})}")
for sig in ("mos", "mc_mos", "p_fair_above_price", "vol_annual", "momentum_126d", "ret_1y", "pe", "p_up_bootstrap"):
    s = [r for r in data if F(r, sig) is not None]
    pooled = spearman([F(r, sig) for r in s], [F(r, "excess_return") for r in s])
    by = defaultdict(list)
    for r in s: by[r["as_of"]].append(r)
    cs = [c for g in by.values() if (c := spearman([F(r, sig) for r in g], [F(r, "excess_return") for r in g])) is not None]
    print(f"{sig:20s} n={len(s):4d} pooled rho={pooled if pooled is None else round(pooled,3)}  "
          f"mean cross-sectional rho={round(st.mean(cs),3) if cs else None} over {len(cs)} dates "
          f"(positive in {sum(c>0 for c in cs)})")

dcf = [r for r in data if r["mos"]]
print("\nExcess return by point-DCF margin-of-safety bucket (63d vs SPY):")
for lo, hi in ((-9, -2), (-2, -1), (-1, -.5), (-.5, 0), (0, .15), (.15, 9)):
    g = [F(r, "excess_return") for r in dcf if lo <= F(r, "mos") < hi]
    if g: print(f"  MoS [{lo:+.2f},{hi:+.2f}) n={len(g):4d} mean={st.mean(g):+.3f} median={st.median(g):+.3f} beat SPY={sum(x>0 for x in g)/len(g):.0%}")
print("\nBuy-gate reachability, share of DCF rows passing:")
for t in (.15, .10, 0, -.25, -.5, -1.0):
    g = [F(r, "excess_return") for r in dcf if F(r, "mos") >= t]
    print(f"  MoS >= {t:+.2f}: {len(g)/len(dcf):.1%} of rows" + (f", mean excess {st.mean(g):+.3f}, beat SPY {sum(x>0 for x in g)/len(g):.0%}" if g else ""))
allx = [F(r, "excess_return") for r in data]
print(f"\nAll rows: mean excess {st.mean(allx):+.3f}, beat SPY {sum(x>0 for x in allx)/len(allx):.0%}")
