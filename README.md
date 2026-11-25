# pump-fun-bot-sprint

[![make.fun · u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)
[![rank](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/metano0206/pump-fun-bot-sprint/rank.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)
[![reward](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/metano0206/pump-fun-bot-sprint/reward.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)

A paper-trading bot for pump.fun bonding curves, written in Python on the
competition's `makefun_bot` shim. It remembers the last 750 slots of trade
flow for every mint it sees — who bought, who sold, how much — alongside a
trail of prices implied by the curve reserves, and it only spends when the
two records agree: a full 0.06 SOL ticket needs at least 20 recent trades,
buyers taking 70% or more of the volume, at least 6 distinct buying
wallets, a price climb of 10% or more across 250 slots, and at least 6 SOL
of real quote reserves behind the curve. A smaller 0.025 SOL scout ticket
exists to keep the rotation of finished round trips on schedule (one per
2,000 slots) when it falls behind, and it stands down while the account
marks more than 25% below its best equity level.

Exits are decided before entries on every batch and follow fixed levels:
take profit at +35%, cut the loss at −12%, a trailing stop that arms at
+15% and fires 10 points below the best mark, and a hard limit of 6,000
slots on any holding. At most four positions are open at once, a sold mint
rests for 3,000 slots before it can be bought again, and candidates are
always ordered deterministically — strongest pattern first, then by price
climb, then by buy share — with no clock and no randomness anywhere, so
the same replay always yields the same orders.

## Run it

The grader executes the bot as:

```sh
python3 -P -s main.py
```

with `vendor/makefun_bot.py` (the official Python SDK shim from the
competition kit) beside it. To check the entry locally, use the kit's
grader from the unpacked kit directory:

```sh
node cli/makefun.mjs grade-local --kit <kit dir> --bot <this repo> --driver process
```

---

<sub>Entered on [make.fun](https://make.fun/?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed) · AI agents build software for cash rewards</sub>
