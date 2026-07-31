# Changelog

All notable changes to the Genesis trading bot are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- (nothing yet)

### Changed
- (nothing yet)

### Fixed
- (nothing yet)

## [2.1.0] - 2026-07-31

### Added
- **Universal Day Trader profile** — 4th launcher profile: a 3-strategy matrix
  (TrendEngine / MeanReversion / SessionBreakout) across 22 pairs with
  session-aware routing (London/NY, Asian, open windows).
- **Gatekeeper Trend Guard (Rule 1) + Minimum SL Floor (Rule 2)** — lightweight
  profile-aware ADX / EMA-50 / ATR gatekeeper indicators on the H1 (or M15 for
  scalper) timeframe, plus a minimal ATR-only helper for breakeven/trailing logic.
- **Liquidity sweep detection** — `DataFetcher.detect_sweeps()` finds sellside
  (SSL) and buyside (BSL) wicks ≥ 1× ATR beyond swing pivots with rejection.
- **`GET /api/candles/{symbol}`** — OHLCV route powering the position-detail
  mini candlestick chart.
- **Position Details modal** — click any active-position row to inspect it, view
  a live mini candlestick tick chart, adjust trailing stops, or close manually.
- **Live price polling** — real-time WebSocket/polling data bound to the CURRENT
  and PROFIT cells of the Active Positions table.
- **Engine state toggle** — the PAUSED / RUNNING status is now an interactive
  button that sends start/pause commands to the backend API.
- **Switch Profile button** — returns to the profile picker from the dashboard.
- **Extended Trade History modal** — historical metrics, timestamps, closed PnL,
  win rates, and an in-dashboard Recent History section.
- **Liquid-glass UI overhaul** — specular top-border highlights, polished inner
  shadows, vibrant borders, and multi-layered backdrop blurs across the dashboard
  and the profile-picker launcher (obsidian theme, 4-column responsive grid).
- **Offline charting** — Chart.js 4.4.1 and chartjs-chart-financial 0.2.1 are now
  vendored under `dashboard/frontend/vendor/` so candlesticks render with no CDN.
- **`scripts/build_deploy.py`** — one-shot PyInstaller build + three-copy deploy
  sync + recursive verification (see docs/LAUNCHER.md).

### Changed
- **Launcher version** bumped to `2.1.0` (`Genesis.exe --version`).
- **MT5 credential lock** — the connector now refuses to start unless the account
  server is `MetaQuotes-Demo`; profile switching never changes accounts.
- **Fast-boot optimizations** — bridge polling tightened to 100 ms, dashboard
  navigation timeout reduced to 15 s, and profile launch skips the redundant full
  preflight re-check.

### Fixed
- **Chart.js modal rendering** — charts re-instantiate and `chart.resize()` runs
  when the expanded Performance Analytics modal opens.
- **Bridge method names** — picker calls updated to pywebview 6 snake_case API
  (`select_profile`, `get_profile_details`, `run_preflight_check`).

[Unreleased]: https://github.com/l4vrboyc-afk/genesis/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/l4vrboyc-afk/genesis/releases/tag/v2.1.0
