# merchant-sweep

Back-end service that tracks daily high prices of cryptocurrency tokens held in merchant wallets and alerts merchants when prices drop below configurable thresholds, offering one-tap conversion into a designated stablecoin.

**Status:** Pre-MVP. See [`docs/SPEC.md`](http://docs/SPEC.md) for the full design.

## What it does

A merchant accepts a cryptocurrency token in exchange for goods. Crypto prices move. This service:

1. Tracks the daily high price of each token in the merchant's holdings (1-hour close granularity).  
2. Generates an alert when a token's price drops by a merchant-configurable percentage below its daily high.  
3. Offers the merchant three responses: **Sweep** (convert to stablecoin), **HODL** (do nothing today), **Snooze 3%** (re-alert if it drops another 3%).  
4. Optionally auto-sweeps on alert timeout (opt-in only).  
5. Supports scheduled sweeps as an alternative to threshold-based alerts.

## Architecture in one paragraph

A swappable `PriceProvider` polls prices (MVP: CoinGecko). A `DailyHighTracker` maintains hourly closes per `(merchant, token, day)` and recomputes the daily high. An `AlertEngine` evaluates drawdown against configurable thresholds and emits alerts via an in-app overlay. A swappable `SweepVenue` executes conversions (MVP: Coinbase Advanced Trade, trade-only API scope). A `Backtest` harness replays historical prices through the same code paths, with HODL and immediate-sweep counterfactuals and sweep-regret metrics.

## Development

See [`CLAUDE.md`](http://CLAUDE.md) for conventions and workflow.

```shell
git clone git@github.com:RadioUFHM/gainsweep.git
cd gainsweep
pip install -e ".[dev]"
pytest
```

## License

\[TBD\]  
