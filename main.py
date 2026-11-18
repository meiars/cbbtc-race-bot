# cbBTC race bot (Python 3.13, standard library only, on the kit's SDK shim).
#
# What the dev window's tape says (measured on its 1,440 bars before this
# strategy was written): rallies fade and dips bounce, both fast. After a
# 15-minute rise of 50 bps the next 15 minutes averaged -35 bps; after a
# 15-minute drop of 50 bps they averaged +28 bps, winning 74% of the time,
# and the bounce was spent within the hour. Slow trend-following and
# buy-and-hold both bled the roughly 26 bps a full-size round trip costs.
# So the bot does one thing: it buys sharp drops, and only confirmed
# ones. The close must be down at least 35 bps over 15 minutes AND at
# least 1.5 standard deviations under its one-hour mean. It spends half
# the equity, then leaves fast: +50 bps, -40 bps, or 25 bars, whichever
# comes first, because the bounce does not survive a longer hold after
# fees. A 10-bar cooldown keeps it from churning the same dip, a
# drawdown brake parks it in cash long before the race's 25% guardrail,
# and a small late rescue trade exists only so a window with no dip at
# all still leaves it with the one filled trade the guardrail asks for.
# Cash is the default position: most minutes it does nothing, which is
# what survives a falling window.
#
# Locally: the kit grades the dev window; this file only speaks the
# protocol through vendor/makefun_bars.py.

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYM = "CBBTC"

PARAMS = {
    "dip_bps": -35,        # enter when the 15-minute return is at or below this
    "dip_span": 15,        # minutes over which the entry drop is measured
    "z_req": -1.5,         # also require the z-score at or below this
    "z_window": 60,        # rolling window for the z-score
    "size_frac": 0.5,      # share of equity spent per entry
    "hold_bars": 25,       # exit after this many bars in the trade
    "take_bps": 50,        # exit when the trade is up this much
    "stop_bps": 40,        # exit when the trade is down this much
    "cool": 10,            # bars to wait after an exit before re-entering
    "brake_dd": 0.10,      # stop opening trades beyond this equity drawdown
    "cutoff": 1400,        # no entries at or after this bar index
    "rescue_at": 1200,     # with no trade yet, relax the dip past this bar
    "rescue_dip_bps": -15,
    "rescue_size": 0.15,
    "slip_bps": 500,       # max_slippage_bps sent with every order
}

_hist = {
    "closes": [],
    "entry_seq": -1,
    "exit_seq": -10**9,
    "trades": 0,
    "peak": 0,
    "braked": False,
}


def _zscore(closes, window):
    if len(closes) < window:
        return None
    w = closes[-window:]
    mean = sum(w) / window
    var = sum((v - mean) ** 2 for v in w) / window
    if var <= 0:
        return None
    return (closes[-1] - mean) / var ** 0.5


def on_batch(state, batch):
    p = PARAMS
    close = state.close(SYM)
    if close is None:
        return []
    closes = _hist["closes"]
    closes.append(close)
    seq = state.seq

    if state.equity > _hist["peak"]:
        _hist["peak"] = state.equity
    if _hist["peak"] > 0 and state.equity < _hist["peak"] * (1 - p["brake_dd"]):
        _hist["braked"] = True

    holding = state.positions.get(SYM)
    if holding is not None and holding.qty > 0:
        entry_price = holding.cost * 10_000_000 / holding.qty
        pnl_bps = (close / entry_price - 1.0) * 10_000 if entry_price > 0 else 0.0
        held = seq - _hist["entry_seq"]
        if pnl_bps >= p["take_bps"] or pnl_bps <= -p["stop_bps"] or held >= p["hold_bars"]:
            _hist["exit_seq"] = seq
            return [{
                "side": "sell",
                "symbol": SYM,
                "qty": holding.qty,
                "max_slippage_bps": p["slip_bps"],
            }]
        return []

    # Flat: look for a sharp, confirmed drop to buy.
    if state.warming_up or _hist["braked"]:
        return []
    if seq >= p["cutoff"] or seq - _hist["exit_seq"] < p["cool"]:
        return []
    span = p["dip_span"]
    if len(closes) <= span:
        return []
    ret_bps = (close / closes[-1 - span] - 1.0) * 10_000
    dip = p["dip_bps"]
    size = p["size_frac"]
    if ret_bps > dip:
        if not (seq >= p["rescue_at"] and _hist["trades"] == 0 and ret_bps <= p["rescue_dip_bps"]):
            return []
        size = p["rescue_size"]
    else:
        z = _zscore(closes, p["z_window"])
        if z is None or z > p["z_req"]:
            return []
    notional = int(state.equity * size)
    notional = min(notional, state.cash, state.weight_room(SYM))
    if notional < 1_000_000:
        return []
    _hist["entry_seq"] = seq
    _hist["trades"] += 1
    return [{
        "side": "buy",
        "symbol": SYM,
        "notional_quote_units": notional,
        "max_slippage_bps": p["slip_bps"],
    }]


if __name__ == "__main__":
    makefun_bars.run(on_batch)
