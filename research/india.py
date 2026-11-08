"""Indian market strategy tests (daily candles, Yahoo Finance data, walk-forward = blind).

Run on the server:  .venv/bin/python -m research.india
Costs per side: ETFs 0.10%, stocks 0.20%, index futures 0.03% (+ roll), all incl. slippage.
Cash earns 6%/yr when the strategy is out of the market (liquid fund / LIQUIDBEES).
Note: Nifty 50 stock list is TODAY's list, so stock results are a bit too optimistic (survivorship).
"""
import math
import os
import pickle
import sys

import numpy as np
import pandas as pd

D = 252
CASH = 0.06 / D
CACHE = "data/research/india_prices.pkl"

INDEX = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}
ETFS = {"NIFTYBEES": "NIFTYBEES.NS", "BANKBEES": "BANKBEES.NS", "JUNIORBEES": "JUNIORBEES.NS",
        "GOLDBEES": "GOLDBEES.NS", "MON100": "MON100.NS"}
NIFTY50 = ("RELIANCE TCS HDFCBANK ICICIBANK INFY BHARTIARTL ITC SBIN LT HINDUNILVR BAJFINANCE "
           "KOTAKBANK HCLTECH MARUTI SUNPHARMA AXISBANK M&M ULTRACEMCO TITAN NTPC ONGC ASIANPAINT "
           "POWERGRID TATAMOTORS ADANIPORTS ADANIENT WIPRO BAJAJFINSV COALINDIA NESTLEIND JSWSTEEL "
           "TATASTEEL GRASIM TECHM HINDALCO SBILIFE BAJAJ-AUTO HDFCLIFE CIPLA DRREDDY EICHERMOT "
           "BRITANNIA APOLLOHOSP TATACONSUM HEROMOTOCO BPCL INDUSINDBK SHRIRAMFIN TRENT BEL").split()


# ---------------- data ----------------
def load():
    if os.path.exists(CACHE):
        return pickle.load(open(CACHE, "rb"))
    import yfinance as yf
    tick = {**INDEX, **ETFS, **{s: s + ".NS" for s in NIFTY50}}
    px = {}
    for name, t in tick.items():
        try:
            df = yf.download(t, period="max", interval="1d", auto_adjust=True, progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df[["Open", "High", "Low", "Close"]].dropna()
            df = df[df["Close"] > 0]
            if len(df) > 500:
                px[name] = df
            print(name, len(df), df.index[0].date() if len(df) else "-", flush=True)
        except Exception as e:  # noqa: BLE001
            print("fail", name, e, flush=True)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    pickle.dump(px, open(CACHE, "wb"))
    return px


def clean(px):
    """Yahoo has broken days for some Indian ETFs/stocks (splits not adjusted). Remove impossible jumps."""
    out = {}
    for n, df in px.items():
        thr = 0.20 if (n in ETFS or n in INDEX) else 0.40
        r = df["Close"].pct_change().fillna(0)
        bad = r.abs() > thr
        if bad.any():
            print(f"cleaned {n}: {int(bad.sum())} bad days", flush=True)
        r[bad] = 0.0
        newc = df["Close"].iloc[0] * (1 + r).cumprod()
        f = newc / df["Close"]
        out[n] = df.mul(f, axis=0)
    return out


# ---------------- stats ----------------
def stats(r):
    r = pd.Series(r).dropna()
    if len(r) < 50:
        return None
    eq = (1 + r).cumprod()
    yrs = len(r) / D
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    sh = r.mean() / (r.std() or 1e-9) * math.sqrt(D)
    return {"cagr": cagr, "dd": dd, "sharpe": sh, "yrs": yrs}


def fmt(name, s, extra=""):
    if not s:
        return f"{name:44s} (not enough data)"
    return (f"{name:44s} {100*s['cagr']:6.1f}%/yr  maxDD {100*s['dd']:5.0f}%  "
            f"Sharpe {s['sharpe']:4.2f}  {s['yrs']:4.1f}y {extra}")


# ---------------- single-instrument signals ----------------
def ema(x, n):
    return x.ewm(span=n, adjust=False).mean()


def sig_trend(df, f, s):
    c = df["Close"]
    return (ema(c, f) > ema(c, s)).astype(int)


def sig_breakout(df, N, f, s):
    c, h = df["Close"].values, df["High"].values
    ef, es = ema(df["Close"], f).values, ema(df["Close"], s).values
    out, pos = np.zeros(len(c), int), 0
    for i in range(max(N, s), len(c)):
        if pos == 0 and c[i] > h[i - N:i].max():
            pos = 1
        elif pos == 1 and ef[i] < es[i]:
            pos = 0
        out[i] = pos
    return pd.Series(out, df.index)


def sig_longshort(df, f, s):
    c = df["Close"]
    return np.sign(ema(c, f) - ema(c, s)).astype(int)


def run(df, pos, cost, short_ok=False, lev=1.0):
    """pos decided at close of day t is held during day t+1. Out of market earns cash."""
    r = df["Close"].pct_change().fillna(0)
    p = pos.shift(1).fillna(0)
    if not short_ok:
        p = p.clip(lower=0)
    turn = p.diff().abs().fillna(0)
    ret = p * r * lev - turn * cost * lev + (1 - p.abs()).clip(lower=0) * CASH
    return ret


GRIDS = {
    "trend": [(f, s) for f in (10, 20, 50) for s in (50, 100, 200) if f < s],
    "breakout": [(N, f, s) for N in (20, 50, 100) for f, s in ((20, 100), (50, 200))],
    "longshort": [(f, s) for f in (10, 20, 50) for s in (50, 100, 200) if f < s],
}
SIG = {"trend": sig_trend, "breakout": sig_breakout, "longshort": sig_longshort}


def walk_forward(df, kind, cost, train=3 * D, test=D, lev=1.0):
    short = kind == "longshort"
    out, i, chosen = [], train, []
    while i < len(df):
        tr = df.iloc[i - train:i]
        best, bs = None, -9
        for p in GRIDS[kind]:
            st = stats(run(tr, SIG[kind](tr, *p), cost, short))
            if st and st["sharpe"] > bs:
                best, bs = p, st["sharpe"]
        win = df.iloc[max(0, i - 400):i + test]
        ret = run(win, SIG[kind](win, *best), cost, short, lev)
        out.append(ret.loc[df.index[i]:df.index[min(i + test, len(df)) - 1]])
        chosen.append(best)
        i += test
    return pd.concat(out), chosen


# ---------------- portfolios ----------------
def etf_rotation(px, names, look=126, top=1, cost=0.001, filt=200):
    """Each month hold the ETF(s) with the best 6-month return, only if above its 200-day average."""
    c = pd.DataFrame({n: px[n]["Close"] for n in names if n in px}).dropna()
    r = c.pct_change().fillna(0)
    mom = c / c.shift(look) - 1
    above = c > c.rolling(filt).mean()
    w = pd.DataFrame(0.0, index=c.index, columns=c.columns)
    cur = pd.Series(0.0, index=c.columns)
    month = None
    for d in c.index[max(look, filt):]:
        if d.month != month:
            month = d.month
            m = mom.loc[d].where(above.loc[d] & (mom.loc[d] > 0)).dropna().sort_values(ascending=False)
            cur = pd.Series(0.0, index=c.columns)
            for n in m.index[:top]:
                cur[n] = 1.0 / top
        w.loc[d] = cur
    wp = w.shift(1).fillna(0)
    turn = wp.diff().abs().sum(axis=1).fillna(0)
    ret = (wp * r).sum(axis=1) - turn * cost + (1 - wp.sum(axis=1)) * CASH
    return ret.iloc[max(look, filt):]


def stock_momentum(px, top=10, look=252, skip=21, cost=0.002, regime=True):
    """Monthly: buy the top-N Nifty 50 stocks by 12-1 month return; go to cash if Nifty < 200-day avg."""
    names = [s for s in NIFTY50 if s in px]
    c = pd.DataFrame({n: px[n]["Close"] for n in names})
    c = c.loc[c.index >= "2005-01-01"].ffill(limit=5)
    nifty = px["NIFTY"]["Close"].reindex(c.index).ffill()
    r = c.pct_change().fillna(0)
    mom = c.shift(skip) / c.shift(look) - 1
    w = pd.DataFrame(0.0, index=c.index, columns=c.columns)
    cur, month = pd.Series(0.0, index=c.columns), None
    ma = nifty.rolling(200).mean()
    for d in c.index[look + 1:]:
        if d.month != month:
            month = d.month
            cur = pd.Series(0.0, index=c.columns)
            if not regime or nifty.loc[d] > ma.loc[d]:
                m = mom.loc[d].dropna().sort_values(ascending=False)
                for n in m.index[:top]:
                    cur[n] = 1.0 / top
        w.loc[d] = cur
    wp = w.shift(1).fillna(0)
    turn = wp.diff().abs().sum(axis=1).fillna(0)
    ret = (wp * r).sum(axis=1) - turn * cost + (1 - wp.sum(axis=1)) * CASH
    return ret.iloc[look + 1:]


# ---------------- main ----------------
def main():
    px = clean(load())
    print("\n=== BUY & HOLD (price only, no dividends) ===")
    for n in ("NIFTY", "BANKNIFTY", "NIFTYBEES", "JUNIORBEES", "GOLDBEES", "MON100"):
        if n in px:
            print(fmt(n + " buy & hold", stats(px[n]["Close"].pct_change().dropna())))

    print("\n=== SINGLE INDEX, WALK-FORWARD (tune 3y, trade next 1y blind) ===")
    for n, cost in (("NIFTY", 0.0005), ("BANKNIFTY", 0.0005), ("NIFTYBEES", 0.001), ("JUNIORBEES", 0.001)):
        if n not in px:
            continue
        df = px[n]
        for kind in ("trend", "breakout", "longshort"):
            ret, ch = walk_forward(df, kind, cost)
            print(fmt(f"{n} {kind}", stats(ret), f"settings {ch[-1]}"))
        ret, _ = walk_forward(df, "longshort", cost, lev=2.0)
        print(fmt(f"{n} longshort 2x (futures)", stats(ret)))

    print("\n=== ETF ROTATION (monthly, best 6m momentum, 200-day filter) ===")
    for names, top in ((["NIFTYBEES", "JUNIORBEES", "BANKBEES", "GOLDBEES", "MON100"], 1),
                       (["NIFTYBEES", "JUNIORBEES", "BANKBEES", "GOLDBEES", "MON100"], 2),
                       (["NIFTYBEES", "JUNIORBEES", "GOLDBEES"], 1)):
        print(fmt(f"rotation top{top} of {len(names)}", stats(etf_rotation(px, names, top=top))))

    print("\n=== NIFTY 50 STOCK MOMENTUM (monthly) ===")
    for top in (5, 10):
        for regime in (True, False):
            r = stock_momentum(px, top=top, regime=regime)
            print(fmt(f"top{top} momentum {'+ Nifty filter' if regime else 'always invested'}", stats(r)))
            if top == 10 and regime:
                yr = (1 + r).groupby(r.index.year).prod() - 1
                print("   by year:", " ".join(f"{y}:{100*v:+.0f}%" for y, v in yr.items()))

    print("\nnote: stock list = today's Nifty 50 (survivorship bias) -> real results somewhat lower.")


if __name__ == "__main__":
    sys.exit(main())
