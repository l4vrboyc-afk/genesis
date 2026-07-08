"""
Backtesting Engine — Simulates strategy performance on historical data.
Loads candle data, evaluates signals, manages virtual positions, and logs metrics.
"""

import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Optional
from loguru import logger

from bot.config.settings import settings, TradeDirection, MarketRegime
from bot.strategies.base_strategy import TradeSignal
from bot.strategies.strategy_selector import StrategySelector


class Backtester:
    """Historical backtesting simulator for Genesis strategies."""

    def __init__(self, initial_capital: float = 10000.0):
        self.initial_capital = initial_capital
        self.balance = initial_capital
        self.equity = initial_capital
        self.peak = initial_capital
        self.max_drawdown = 0.0
        
        self.open_positions = []
        self.closed_trades = []
        self.ticket_counter = 1000

    def generate_synthetic_data(self, symbol: str, days: int = 30) -> tuple:
        """Generates aligned synthetic HTF (H4) and ETF (M15) data for dry runs."""
        logger.info(f"📈 Generating {days} days of synthetic data for {symbol}...")
        
        start_date = datetime.now() - timedelta(days=days)
        
        # M15 parameters
        m15_intervals = int(days * 24 * 4)
        m15_dates = [start_date + timedelta(minutes=15 * i) for i in range(m15_intervals)]
        
        # Simulating random walk with regimes (trending vs ranging)
        np.random.seed(42)
        prices = [1.10000]
        
        # Create regimes: 40% trending, 60% ranging
        regimes = np.random.choice([0, 1], size=days, p=[0.6, 0.4])
        
        for idx in range(1, m15_intervals):
            day_idx = idx // 96
            regime = regimes[day_idx]
            
            if regime == 1:  # Trending: upward bias
                change = np.random.normal(0.00008, 0.0004)
            else:  # Ranging
                change = np.random.normal(0, 0.0003)
                
            prices.append(max(1.0500, prices[-1] + change))

        # Build M15 DataFrame
        m15_df = pd.DataFrame(index=m15_dates)
        m15_df["close"] = prices
        m15_df["open"] = m15_df["close"].shift(1).fillna(1.1000)
        m15_df["high"] = m15_df[["open", "close"]].max(axis=1) + np.random.exponential(0.0002, len(m15_df))
        m15_df["low"] = m15_df[["open", "close"]].min(axis=1) - np.random.exponential(0.0002, len(m15_df))
        m15_df["volume"] = np.random.randint(100, 2500, len(m15_df))
        
        # Build H4 DataFrame
        h4_df = m15_df.resample("4h").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum"
        }).dropna()

        # Add indicators (using DataFetcher equivalent calculations)
        from bot.core.data_fetcher import DataFetcher
        mock_fetcher = DataFetcher(None)
        
        m15_df = mock_fetcher.calculate_indicators(m15_df)
        m15_df = mock_fetcher.detect_order_blocks(m15_df)
        m15_df = mock_fetcher.detect_fair_value_gaps(m15_df)
        
        h4_df = mock_fetcher.calculate_indicators(h4_df)
        
        return h4_df, m15_df

    def run(self, htf_df: pd.DataFrame, etf_df: pd.DataFrame, symbol: str):
        """Run the backtest loop bar-by-bar."""
        selector = StrategySelector()
        
        logger.info(f"🏁 Starting backtest simulation on {len(etf_df)} bars...")

        # Align H4 index to M15 index to simulate streaming
        for idx in range(200, len(etf_df)):
            current_time = etf_df.index[idx]
            m15_slice = etf_df.iloc[:idx+1]
            
            # Slice H4 data up to current timestamp
            h4_slice = htf_df[htf_df.index <= current_time]
            if len(h4_slice) < 50:
                continue

            current_row = etf_df.iloc[idx]
            current_price = {
                "bid": current_row["close"] - 0.0001,
                "ask": current_row["close"] + 0.0001,
            }

            # 1. Manage existing positions (check SL/TP)
            self._update_open_positions(current_row, current_time)

            # 2. Check for new trade entries
            if len(self.open_positions) < settings.max_open_positions:
                signal = selector.get_signal(symbol, h4_slice, m15_slice, current_price)
                if signal and signal.direction != TradeDirection.HOLD:
                    # Execute entry
                    self._open_simulated_trade(signal, current_time)

        # Close any remaining open positions at final price
        final_row = etf_df.iloc[-1]
        self._close_remaining(final_row, etf_df.index[-1])

        self._print_results()

    def _open_simulated_trade(self, signal: TradeSignal, time: datetime):
        """Open a virtual position."""
        # Simple simulated position sizing
        risk_amt = self.balance * 0.01
        sl_dist = abs(signal.entry_price - signal.stop_loss)
        
        if sl_dist == 0:
            return
            
        lots = round(risk_amt / (sl_dist * 100000), 2)
        lots = max(0.01, lots)

        self.ticket_counter += 1
        pos = {
            "ticket": self.ticket_counter,
            "symbol": signal.symbol,
            "direction": signal.direction,
            "volume": lots,
            "entry_price": signal.entry_price,
            "sl": signal.stop_loss,
            "tp": signal.take_profit,
            "strategy": signal.strategy_name,
            "open_time": time,
        }
        self.open_positions.append(pos)

    def _update_open_positions(self, bar, time: datetime):
        """Check open positions against current bar high/low for exit validation."""
        remaining = []
        for p in self.open_positions:
            closed = False
            exit_price = 0.0
            profit = 0.0
            reason = ""

            if p["direction"] == TradeDirection.BUY:
                # Check Stop Loss hit
                if bar["low"] <= p["sl"]:
                    closed = True
                    exit_price = p["sl"]
                    reason = "Stop Loss"
                # Check Take Profit hit
                elif bar["high"] >= p["tp"]:
                    closed = True
                    exit_price = p["tp"]
                    reason = "Take Profit"
                
                if closed:
                    # Profit (assuming EURUSD contract size = 100k)
                    profit = (exit_price - p["entry_price"]) * p["volume"] * 100000
                    
            elif p["direction"] == TradeDirection.SELL:
                # Check Stop Loss hit
                if bar["high"] >= p["sl"]:
                    closed = True
                    exit_price = p["sl"]
                    reason = "Stop Loss"
                # Check Take Profit hit
                elif bar["low"] <= p["tp"]:
                    closed = True
                    exit_price = p["tp"]
                    reason = "Take Profit"
                
                if closed:
                    profit = (p["entry_price"] - exit_price) * p["volume"] * 100000

            if closed:
                # Settle trade
                self.balance += profit
                self.peak = max(self.peak, self.balance)
                dd = (self.peak - self.balance) / self.peak
                self.max_drawdown = max(self.max_drawdown, dd)

                p["exit_price"] = exit_price
                p["profit"] = profit
                p["close_time"] = time
                p["reason"] = reason
                self.closed_trades.append(p)
            else:
                remaining.append(p)
                
        self.open_positions = remaining

    def _close_remaining(self, final_bar, time: datetime):
        """Force-close any open trades at backtest end."""
        exit_price = final_bar["close"]
        for p in self.open_positions:
            if p["direction"] == TradeDirection.BUY:
                profit = (exit_price - p["entry_price"]) * p["volume"] * 100000
            else:
                profit = (p["entry_price"] - exit_price) * p["volume"] * 100000
                
            self.balance += profit
            p["exit_price"] = exit_price
            p["profit"] = profit
            p["close_time"] = time
            p["reason"] = "Forced End"
            self.closed_trades.append(p)
        self.open_positions = []

    def _print_results(self):
        """Print backtest performance summary."""
        total = len(self.closed_trades)
        if total == 0:
            print("\n❌ Backtest ended with 0 trades taken.")
            return

        wins = sum(1 for t in self.closed_trades if t["profit"] > 0)
        losses = sum(1 for t in self.closed_trades if t["profit"] <= 0)
        win_rate = wins / total
        
        gross_profit = sum(t["profit"] for t in self.closed_trades if t["profit"] > 0)
        gross_loss = abs(sum(t["profit"] for t in self.closed_trades if t["profit"] < 0))
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        net_profit = self.balance - self.initial_capital

        print("\n" + "="*50)
        print("📊 GENESIS BACKTEST SIMULATION RESULTS")
        print("="*50)
        print(f"Initial Capital:   ${self.initial_capital:.2f}")
        print(f"Ending Capital:    ${self.balance:.2f}")
        print(f"Net Profit:        ${net_profit:.2f} ({net_profit/self.initial_capital*100:.2f}%)")
        print(f"Max Drawdown:      {self.max_drawdown*100:.2f}%")
        print(f"Total Trades:      {total}")
        print(f"Wins / Losses:     {wins} / {losses}")
        print(f"Win Rate:          {win_rate*100:.2f}%")
        print(f"Profit Factor:     {pf:.2f}")
        print("="*50)
        
        # Print breakdown by strategy
        strategies = {}
        for t in self.closed_trades:
            strat = t["strategy"]
            if strat not in strategies:
                strategies[strat] = {"count": 0, "wins": 0, "pnl": 0.0}
            strategies[strat]["count"] += 1
            if t["profit"] > 0:
                strategies[strat]["wins"] += 1
            strategies[strat]["pnl"] += t["profit"]

        print("Strategy Breakdown:")
        for name, data in strategies.items():
            wr = data["wins"] / data["count"] * 100
            print(f"- {name: <25} | Trades: {data['count']: <3} | WR: {wr:.1f}% | P&L: ${data['pnl']:.2f}")
        print("="*50 + "\n")


if __name__ == "__main__":
    backtest = Backtester(10000.0)
    htf, etf = backtest.generate_synthetic_data("EURUSD", days=60)
    backtest.run(htf, etf, "EURUSD")
