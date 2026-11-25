"""Bot Sprint entry: a momentum reader for the pump.fun paper replay.

How it thinks
-------------
Every batch that arrives carries other traders' actions on the
bonding curves. This bot keeps, per mint, a short rolling tape of
those actions and a trail of the prices the curve reserves imply.
A purchase is only made when the two records corroborate each
other: the tape shows a crowd of distinct buyers taking most of
the recent volume *and* the price has climbed over the lookback
span. Weak versions of the same pattern are traded only in a
smaller size, and only when the bot owes the schedule some
finished round trips.

Risk is handled by shape rather than by prediction: tickets are
small next to the 10 SOL paper purse, at most four positions are
open at once, every holding has pre-set exit levels and a maximum
age, a trailing stop banks part of a strong run instead of
watching it evaporate, and a mint that has been sold is left
alone for a while.
Nothing here reads a clock or a random source, and every choice
is made from sorted candidate lists, so a replay of the same
batches always produces the same orders.
"""

import os
import sys
from collections import deque

# The grader launches the bot with -P, which drops the script's own
# folder from sys.path, so point at the vendored SDK explicitly.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bot  # noqa: E402

SOL = makefun_bot.LAMPORTS_PER_SOL

# ---- order sizing -------------------------------------------------------
BIG_ORDER = 60_000_000        # 0.06 SOL behind a full-strength pattern
SMALL_ORDER = 25_000_000      # 0.025 SOL behind a scout pattern
MIN_ORDER = 12_000_000        # below 0.01 SOL a round trip does not count
MAX_OPEN = 4                  # harness ceiling is 5; keep a spare slot
POOL_FLOOR = 6 * SOL          # real quote reserves a mint must show

# ---- pattern thresholds ---------------------------------------------------
FLOW_WINDOW = 750             # slots of trade flow remembered per mint
DRIFT_SPAN = 250              # slots across which the price climb is read
FULL_TRADES = 20
FULL_BUYERS = 6
FULL_BUY_SHARE = 0.70
FULL_DRIFT = 0.10
SCOUT_TRADES = 5
SCOUT_BUY_SHARE = 0.58
SCOUT_DRIFT = 0.03

# ---- exit levels ------------------------------------------------------------
TAKE_PROFIT = 0.35            # close a holding that marks +35%
CUT_LOSS = -0.12              # close one that marks -12%
TRAIL_ARM = 0.15              # trailing stop arms once a holding marks +15%
TRAIL_GIVEBACK = 0.10         # ...and fires 10 points under its best mark
MAX_HOLD = 6000               # slots; nothing is held longer than this
REST_AFTER_SALE = 3000        # slots a sold mint is ignored for

# ---- pacing and brakes ------------------------------------------------------
SLOTS_PER_ROUND_TRIP = 2000   # rhythm the rotation tries to keep
EQUITY_DIP_LIMIT = 0.25       # scouts pause this far under the equity peak

BUY_SLIPPAGE = 1200           # bps
SELL_SLIPPAGE = 2000          # bps

# ---- bot memory ---------------------------------------------------------------
flow = {}           # mint -> deque of (slot, is_buy, sol_amount, wallet)
quotes = {}         # mint -> deque of (slot, price)
first_seen_held = {}  # mint -> slot the current holding was first noticed
peak_ratio = {}       # mint -> best value/cost ratio marked while held
largest_buy = {}    # mint -> biggest quote_lamports we have sent for it
trips_finished = set()  # mints with a completed round trip (count only)
quiet_until = {}    # mint -> slot from which it may be bought again
opening_slot = [None]   # slot of the first batch of the run
equity_high = [0]       # best mark-to-market equity seen so far


def curve_price(curve):
    """Spot price implied by a curve's virtual reserves, or None."""
    if curve is None or curve.virtual_token_reserves <= 0:
        return None
    return curve.virtual_quote_reserves / curve.virtual_token_reserves


def ingest(state, batch):
    """Fold this batch's events into the flow tapes and price trails."""
    now = state.slot
    touched = set()
    for event in batch["events"]:
        mint = event["mint"]
        touched.add(mint)
        if event["event"] == "TradeEvent":
            tape = flow.setdefault(mint, deque())
            tape.append((now, bool(event.get("is_buy")),
                         int(event.get("sol_amount", 0)),
                         event.get("user", "")))
    for mark in batch["marks"]:
        touched.add(mark["mint"])
    for mint in touched:
        price = curve_price(state.curves.get(mint))
        if price is None:
            continue
        trail = quotes.setdefault(mint, deque())
        if not trail or trail[-1][0] != now:
            trail.append((now, price))
    # Trim old history so a long replay cannot grow memory without limit.
    flow_floor = now - FLOW_WINDOW
    for tape in flow.values():
        while tape and tape[0][0] < flow_floor:
            tape.popleft()
    quote_floor = now - FLOW_WINDOW - 5
    for trail in quotes.values():
        while len(trail) > 1 and trail[0][0] < quote_floor:
            trail.popleft()


def flow_stats(mint):
    """(trade count, buy share of volume, distinct buyers) on the tape."""
    tape = flow.get(mint)
    if not tape:
        return 0, 0.0, 0
    buy_sol = 0
    sell_sol = 0
    buyers = set()
    for _slot, is_buy, amount, wallet in tape:
        if is_buy:
            buy_sol += amount
            buyers.add(wallet)
        else:
            sell_sol += amount
    total = buy_sol + sell_sol
    share = buy_sol / total if total > 0 else 0.0
    return len(tape), share, len(buyers)


def price_drift(mint):
    """Relative price change across DRIFT_SPAN, or None if unknowable."""
    trail = quotes.get(mint)
    if not trail or len(trail) < 2:
        return None
    newest_slot, newest_price = trail[-1]
    target = newest_slot - DRIFT_SPAN
    anchor = None
    for slot, price in trail:
        if slot <= target:
            anchor = price
        else:
            break
    if anchor is None or anchor <= 0:
        return None
    return newest_price / anchor - 1.0


def pending_mints(state, side):
    """Mints that already have an order of the given side in flight."""
    result = set()
    for pending in state.pending.values():
        if pending.order["side"] == side:
            result.add(pending.order["mint"])
    return result


def portfolio_value(state):
    """Cash plus every holding valued at its latest curve price."""
    total = state.cash
    for mint, holding in state.positions.items():
        price = curve_price(state.curves.get(mint))
        if price is not None:
            total += int(holding.token_amount * price)
    return total


def exit_reason(state, mint, holding, ratio, peak):
    """Why this holding should be sold now, or None to keep holding."""
    gain = ratio - 1.0
    if gain >= TAKE_PROFIT:
        return "target reached"
    if gain <= CUT_LOSS:
        return "loss cut"
    if peak - 1.0 >= TRAIL_ARM and ratio <= peak - TRAIL_GIVEBACK:
        return "trailing stop"
    if state.slot - first_seen_held.get(mint, state.slot) >= MAX_HOLD:
        return "held too long"
    return None


def plan_exits(state, orders):
    """Append a full sell for every holding whose exit level fired."""
    already_selling = pending_mints(state, "sell")
    exiting = []
    for mint in sorted(state.positions):
        if mint in already_selling:
            continue
        holding = state.positions[mint]
        curve = state.curves.get(mint)
        if mint not in first_seen_held:
            first_seen_held[mint] = state.slot
        if curve is not None and curve.complete:
            continue  # completed curves are settled by the harness itself
        price = curve_price(curve)
        if price is None or holding.cost_lamports <= 0:
            continue
        ratio = holding.token_amount * price / holding.cost_lamports
        peak = peak_ratio.get(mint, ratio)
        if ratio > peak:
            peak = peak_ratio[mint] = ratio
        if exit_reason(state, mint, holding, ratio, peak) is None:
            continue
        orders.append({
            "side": "sell",
            "mint": mint,
            "token_amount": holding.token_amount,
            "max_slippage_bps": SELL_SLIPPAGE,
        })
        exiting.append(mint)
    return exiting


def plan_entries(state, orders, exiting):
    """Score every visible mint and buy the best qualifying patterns."""
    caps = state.hello["caps"]
    fee = state.tx_fee_lamports()
    cash = state.free_cash_lamports()
    # This batch's exit orders are not yet recorded by the shim, so
    # reserve their transaction fees against the spendable cash here.
    cash -= fee * len(exiting)
    committed = set(state.positions) | pending_mints(state, "buy")
    room = MAX_OPEN - len(committed)
    if room <= 0:
        return
    unavailable = committed | set(exiting) | pending_mints(state, "sell")

    # Rotation bookkeeping: the run wants one finished round trip
    # per SLOTS_PER_ROUND_TRIP slots; scouts only trade when behind.
    elapsed = max(0, state.slot - (opening_slot[0] or state.slot))
    trips_due = elapsed // SLOTS_PER_ROUND_TRIP
    behind = len(trips_finished) + 2 < trips_due

    # Equity brake: far under the best mark, stop spending on scouts.
    value = portfolio_value(state)
    if value > equity_high[0]:
        equity_high[0] = value
    dipped = equity_high[0] > 0 and value < equity_high[0] * (1.0 - EQUITY_DIP_LIMIT)

    ranked = []
    for mint in sorted(state.curves):
        if mint in unavailable or state.slot < quiet_until.get(mint, -1):
            continue
        curve = state.curves[mint]
        if curve.complete or curve.real_quote_reserves < POOL_FLOOR:
            continue
        trades, share, buyers = flow_stats(mint)
        drift = price_drift(mint)
        if drift is None:
            continue
        strong = (trades >= FULL_TRADES and share >= FULL_BUY_SHARE
                  and buyers >= FULL_BUYERS and drift >= FULL_DRIFT)
        weak = (behind and not dipped and trades >= SCOUT_TRADES
                and share >= SCOUT_BUY_SHARE and drift >= SCOUT_DRIFT)
        if strong:
            ranked.append((0, -drift, -share, mint))
        elif weak:
            ranked.append((1, -drift, -share, mint))
    ranked.sort()
    for tier, _neg_drift, _neg_share, mint in ranked:
        if room <= 0 or len(orders) >= caps["max_orders_per_batch"]:
            break
        size = BIG_ORDER if tier == 0 else SMALL_ORDER
        amount = min(size, state.max_buy_lamports(mint))
        if amount < MIN_ORDER or cash < amount + fee:
            continue
        orders.append({
            "side": "buy",
            "mint": mint,
            "quote_lamports": amount,
            "max_slippage_bps": BUY_SLIPPAGE,
        })
        if amount > largest_buy.get(mint, 0):
            largest_buy[mint] = amount
        cash -= amount + fee
        room -= 1


def register_departures(state, held_before):
    """Positions that disappeared since last batch end a round trip."""
    for mint in held_before:
        if mint in state.positions:
            continue
        if largest_buy.get(mint, 0) >= MIN_ORDER:
            trips_finished.add(mint)
        quiet_until[mint] = state.slot + REST_AFTER_SALE
        first_seen_held.pop(mint, None)
        peak_ratio.pop(mint, None)


_previous_holdings = [set()]


def handle_batch(state, batch):
    if opening_slot[0] is None:
        opening_slot[0] = state.slot
    register_departures(state, _previous_holdings[0])
    ingest(state, batch)
    orders = []
    exiting = plan_exits(state, orders)
    plan_entries(state, orders, exiting)
    _previous_holdings[0] = set(state.positions)
    return orders


if __name__ == "__main__":
    makefun_bot.run(handle_batch)
