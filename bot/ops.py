"""Housekeeping for the server (run by systemd timers as root, reads .env for Telegram):

  python -m bot.ops health     # hourly: alert if a bot stopped, keeps failing, or the disk is full
  python -m bot.ops weekly     # Sunday: one Telegram report on everything
  python -m bot.ops export     # trade records for tax: data/trades_export.csv (real/testnet trades)
"""
import csv
import datetime as dt
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

from bot.gap_paper import report as gap_report
from bot.gap_paper import tg

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
STATE = "data/ops_state.json"
JOURNAL = os.getenv("JOURNAL_PATH", "data/journal.sqlite")
REALERT_HOURS = 6


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


def _state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def health():
    st, now, problems = _state(), time.time(), []
    if sh("systemctl is-active delta-bot") != "active":
        problems.append(("bot_down", "🚨 Crypto bot (delta-bot) is NOT running. Tell Claude: check bot."))
    fails = sh("journalctl -u delta-bot --since '-1h' --no-pager | grep -c 'tick failed'") or "0"
    if int(fails) >= 20:
        problems.append(("bot_errors", f"⚠️ Crypto bot had {fails} errors in the last hour (Delta/API problem?)."))
    if sh("systemctl show gap-paper -p Result --value") not in ("success", ""):
        problems.append(("gap_failed", "⚠️ Gap paper-trade job failed today. Tell Claude: check gap bot."))
    if sh("systemctl is-enabled gap-paper.timer") != "enabled":
        problems.append(("gap_timer", "⚠️ Gap paper-trade daily timer is switched off."))
    du = shutil.disk_usage("/")
    if du.used / du.total > 0.9:
        problems.append(("disk", f"⚠️ Server disk {100*du.used/du.total:.0f}% full."))
    for key, msg in problems:
        if now - st.get(key, 0) > REALERT_HOURS * 3600:
            tg(msg)
            st[key] = now
    for key in list(st):
        if key not in {k for k, _ in problems}:
            if now - st[key] < 7 * 86400 and st[key] > 0:
                tg(f"✅ Fixed: {key.replace('_', ' ')} is OK again.")
            del st[key]
    os.makedirs("data", exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(st, f)
    print("problems:", [k for k, _ in problems] or "none")


def _baskets():
    if not os.path.exists(JOURNAL):
        return []
    db = sqlite3.connect(JOURNAL)
    rows = db.execute("SELECT id,name,opened_ts,closed_ts,legs,entry_cost,exit_value,pnl,status FROM baskets "
                      "ORDER BY opened_ts").fetchall()
    return rows


def weekly():
    env = os.getenv("DELTA_ENV", "testnet").upper()
    mode = env + (" DRY-RUN" if os.getenv("DRY_RUN", "true").lower() in ("1", "true", "yes") else "")
    rows = [r for r in _baskets() if str(r[1]).startswith("trend:")]
    closed = [r for r in rows if r[8] != "open"]
    open_ = [r for r in rows if r[8] == "open"]
    week = time.time() - 7 * 86400
    wk = [r for r in closed if (r[3] or 0) >= week]
    pnl_all = sum(r[7] or 0 for r in closed)
    pnl_wk = sum(r[7] or 0 for r in wk)
    lines = [f"📊 WEEKLY REPORT {dt.datetime.now(IST):%d %b %Y}",
             "",
             f"🪙 Crypto trend bot [{mode}]: {'running' if sh('systemctl is-active delta-bot') == 'active' else 'NOT RUNNING'}",
             f"  Open trades: {', '.join(r[1].split(':')[1] for r in open_) or 'none (waiting for signal)'}",
             f"  Closed trades: {len(closed)} total, {len(wk)} this week",
             f"  P&L: this week {pnl_wk:+.2f} USD, since start {pnl_all:+.2f} USD",
             f"  Checkpoint: {min(len(closed), 3)}/3 clean trades needed before real money",
             "",
             "📈 Gap idea (paper, Angel One prices):",
             "  " + gap_report(quiet=True).replace("\n", "\n  "),
             f"  Checkpoint: about 20 trades needed before deciding",
             "",
             "💰 Main plan: keep the index fund SIPs going. Nothing else needed from you."]
    tg("\n".join(lines))


def export():
    out = "data/trades_export.csv"
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["opened (IST)", "closed (IST)", "name", "symbol", "side", "size", "contract_value",
                    "entry_price", "entry_cost_usd", "exit_value_usd", "pnl_usd", "status", "account"])
        acct = os.getenv("DELTA_ENV", "testnet") + (" dry-run" if os.getenv("DRY_RUN", "true") == "true" else "")
        for r in _baskets():
            legs = json.loads(r[4] or "[]")
            leg = legs[0] if legs else {}
            ts = lambda x: dt.datetime.fromtimestamp(x, IST).strftime("%Y-%m-%d %H:%M") if x else ""  # noqa: E731
            w.writerow([ts(r[2]), ts(r[3]), r[1], leg.get("symbol"), leg.get("side"), leg.get("size"),
                        leg.get("contract_value"), leg.get("price"), r[5], r[6], r[7], r[8], acct])
    print("written", out)


if __name__ == "__main__":
    {"health": health, "weekly": weekly, "export": export}[sys.argv[1] if len(sys.argv) > 1 else "health"]()
