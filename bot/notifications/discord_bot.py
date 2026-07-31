"""
Discord Bot — Integrates MetaTrader 5 bot with Discord for alerts and remote control.
Uses discord.py commands to interact with a running bot instance.
"""

import asyncio
from datetime import datetime
import discord
from discord.ext import commands
from loguru import logger

from bot.config.settings import settings
from bot.notifications.notification_manager import notification_manager


class DiscordBot(commands.Bot):
    """Discord Bot client for notifying and remote-controlling the trading system."""

    def __init__(self, orchestrator):
        """
        Args:
            orchestrator: The main bot orchestrator instance.
        """
        intents = discord.Intents.default()
        intents.message_content = True
        
        super().__init__(
            command_prefix=settings.discord_command_prefix,
            intents=intents,
            help_command=commands.MinimalHelpCommand()
        )
        self.orchestrator = orchestrator
        self.channel_id = settings.discord_channel_id
        self._queue_task = None

        # Register bot commands programmatically
        self._register_commands()

    async def setup_hook(self):
        """Start the background queue listener task."""
        self._queue_task = self.loop.create_task(self._listen_to_notifications())
        logger.info("🤖 Discord Bot setup hook completed, started queue listener")

    async def on_ready(self):
        """Fires when Discord connection is established."""
        logger.success(f"🤖 Discord Bot logged in as {self.user} (ID: {self.user.id})")
        # Send a startup notification
        await self.send_system_embed(
            "🚀 Bot Started",
            f"**{settings.bot_name}** is online.\n"
            f"Environment: `{'Paper Trading' if settings.paper_trading else 'Live'}`\n"
            f"Active Pairs: `{', '.join(settings.trading_pairs)}`",
            0x2ecc71  # Green
        )

    # ── Notification Listener ────────────────────────────────────────

    async def _listen_to_notifications(self):
        """Listens for items in the notification queue and sends them to Discord."""
        await self.wait_until_ready()

        # Retry channel lookup until Discord caches it. The first on_ready frame
        # often arrives before the channel cache is populated; without retry the
        # listener silently returns and notifications pile up forever.
        while not self.is_closed():
            channel = self.get_channel(self.channel_id)
            if channel is not None:
                break
            logger.warning(
                f"⏳ Discord channel {self.channel_id} not cached yet; retrying in 5s"
            )
            await asyncio.sleep(5)
        else:
            return

        logger.info(f"📣 Connected to Discord channel: {channel.name}")

        while not self.is_closed():
            try:
                # Wait for a notification payload
                payload = await notification_manager.queue.get()

                # Format and send based on notification type — single retry on
                # transient Discord HTTP failures so one bad message doesn't
                # kill the listener
                # Mapping of notification types to handler functions.
                # Some payloads may not include a "data" field (e.g., alerts). Use .get() to avoid KeyError.
                send_map = {
                    "trade_open": (self._send_trade_open_embed, payload.get("data", {})),
                    "trade_close": (self._send_trade_close_embed, payload.get("data", {})),
                    "alert": (self._send_alert_embed, payload),
                    "daily_summary": (self._send_daily_summary_embed, payload.get("data", {})),
                    "custom": (self._send_custom_embed, payload),
                    "regime_change": (self._send_regime_change_embed, payload.get("data", {})),
                }
                entry = send_map.get(payload["type"])
                if entry is not None:
                    send_fn, data = entry
                    for attempt in range(2):
                        try:
                            await send_fn(channel, data)
                            break
                        except discord.HTTPException as e:
                            if attempt == 0:
                                logger.warning(
                                    f"⚠️ Discord send failed (attempt {attempt+1}/2): {e}; retrying"
                                )
                                await asyncio.sleep(2)
                            else:
                                logger.error(f"❌ Discord send giving up after 2 attempts: {e}")

                # Mark as processed
                notification_manager.queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Error sending Discord notification: {e}")
                await asyncio.sleep(1)

    # ── Embed Builders ──────────────────────────────────────────────

    async def send_system_embed(self, title: str, description: str, color: int):
        """Helper to send a quick embed message to the alert channel."""
        if not self.is_ready():
            return
        channel = self.get_channel(self.channel_id)
        if channel:
            embed = discord.Embed(
                title=title,
                description=description,
                color=color,
                timestamp=datetime.now()
            )
            embed.set_footer(text=settings.bot_name)
            await channel.send(embed=embed)

    async def _send_trade_open_embed(self, channel, data: dict):
        """Send embed for trade entry."""
        direction = data.get("direction", "BUY").upper()
        color = 0x3498db if direction == "BUY" else 0xe67e22  # Blue for buy, Orange for sell
        
        embed = discord.Embed(
            title=f"🟢 Trade Opened: {data.get('symbol')}",
            color=color,
            timestamp=datetime.now()
        )
        embed.add_field(name="Direction", value=f"**{direction}**", inline=True)
        embed.add_field(name="Lots", value=f"`{data.get('volume'):.2f}`", inline=True)
        embed.add_field(name="Entry Price", value=f"`{data.get('price'):.5f}`", inline=True)
        embed.add_field(name="Stop Loss", value=f"`{data.get('sl'):.5f}`", inline=True)
        embed.add_field(name="Take Profit", value=f"`{data.get('tp'):.5f}`", inline=True)
        embed.add_field(name="Strategy", value=data.get('strategy', 'Unknown'), inline=True)
        
        if data.get("reason"):
            embed.add_field(name="Reason", value=f"*{data.get('reason')}*", inline=False)
            
        embed.set_footer(text=f"Ticket: {data.get('ticket')}")
        await channel.send(embed=embed)

    async def _send_trade_close_embed(self, channel, data: dict):
        """Send embed for trade exit."""
        profit = data.get("profit", 0.0)
        color = 0x2ecc71 if profit >= 0 else 0xe74c3c  # Green for profit, Red for loss
        emoji = "💰" if profit >= 0 else "📉"
        
        embed = discord.Embed(
            title=f"{emoji} Trade Closed: {data.get('symbol')}",
            color=color,
            timestamp=datetime.now()
        )
        direction = data.get("direction", "BUY").upper()
        embed.add_field(name="Direction", value=direction, inline=True)
        embed.add_field(name="Lots", value=f"`{data.get('volume'):.2f}`", inline=True)
        embed.add_field(name="P&L", value=f"**${profit:.2f}**", inline=True)
        embed.add_field(name="Open Price", value=f"`{data.get('open_price'):.5f}`", inline=True)
        embed.add_field(name="Close Price", value=f"`{data.get('close_price'):.5f}`", inline=True)
        embed.add_field(name="Reason", value=data.get('comment', 'Close'), inline=True)
        
        embed.set_footer(text=f"Ticket: {data.get('ticket')}")
        await channel.send(embed=embed)

    async def _send_alert_embed(self, channel, payload: dict):
        """Send general system alerts."""
        alert_type = payload.get("alert_type", "warning")
        color = 0xe74c3c if alert_type in ("critical", "drawdown") else 0xf1c40f  # Red or Yellow
        
        embed = discord.Embed(
            title=f"⚠️ System Alert: {alert_type.upper()}",
            description=payload.get("message"),
            color=color,
            timestamp=payload.get("timestamp")
        )
        embed.set_footer(text=settings.bot_name)
        await channel.send(embed=embed)

    async def _send_daily_summary_embed(self, channel, data: dict):
        """Send daily trading stats summary."""
        pnl = data.get("total_pnl", 0.0)
        color = 0x9b59b6  # Purple for report
        
        embed = discord.Embed(
            title="📊 Daily Performance Summary",
            color=color,
            timestamp=datetime.now()
        )
        embed.add_field(name="Net P&L", value=f"**${pnl:.2f}**", inline=True)
        embed.add_field(name="Win Rate", value=f"`{data.get('win_rate', 0.0)*100:.1f}%`", inline=True)
        embed.add_field(name="Trades Taken", value=f"`{data.get('total_trades', 0)}`", inline=True)
        embed.add_field(name="Profit Factor", value=f"`{data.get('profit_factor', 0.0)}`", inline=True)
        embed.add_field(name="Max Drawdown", value=f"`{data.get('max_drawdown', 0.0)*100:.2f}%`", inline=True)
        embed.add_field(name="Streak", value=f"{data.get('streak', {}).get('count', 0)} {data.get('streak', {}).get('type', 'none')}", inline=True)
        
        embed.set_footer(text=settings.bot_name)
        await channel.send(embed=embed)

    async def _send_regime_change_embed(self, channel, data: dict):
        """Send regime-change notification embed."""
        old_r = data.get("old_regime", "?").upper()
        new_r = data.get("new_regime", "?").upper()
        embed = discord.Embed(
            title="🔄 Market Regime Change",
            description=f"**{old_r}** → **{new_r}**",
            color=0x9b59b6,  # Purple
            timestamp=datetime.now(),
        )
        embed.add_field(name="ADX", value=f"`{data.get('adx', '—')}`", inline=True)
        embed.add_field(name="ATR Ratio", value=f"`{data.get('atr_ratio', '—')}`", inline=True)
        embed.set_footer(text=settings.bot_name)
        await channel.send(embed=embed)

    async def _send_custom_embed(self, channel, payload: dict):
        """Send custom embed message."""
        embed = discord.Embed(
            title=payload.get("title", "Genesis Notification"),
            description=payload.get("message", ""),
            color=payload.get("color", 0x3498db),
            timestamp=payload.get("timestamp")
        )
        embed.set_footer(text=settings.bot_name)
        await channel.send(embed=embed)

    # ── Command Registration ────────────────────────────────────────

    def _register_commands(self):
        """Registers the Discord commands."""
        
        @self.command(name="status", help="Get connection status, balance, and running state")
        async def cmd_status(ctx):
            stats = await self.orchestrator.get_status()
            embed = discord.Embed(
                title="⚙️ Genesis Bot Status",
                color=0x3498db,
                timestamp=datetime.now()
            )
            embed.add_field(name="Status", value="🟢 RUNNING" if not stats["paused"] else "⏸️ PAUSED", inline=True)
            embed.add_field(name="MT5 Connected", value="✅ YES" if stats["mt5_connected"] else "❌ NO", inline=True)
            embed.add_field(name="Account", value=f"`{stats['account_login']}`", inline=True)
            embed.add_field(name="Balance", value=f"**${stats['balance']:.2f}**", inline=True)
            embed.add_field(name="Equity", value=f"**${stats['equity']:.2f}**", inline=True)
            embed.add_field(name="Daily P&L", value=f"**${stats['daily_pnl']:.2f}**", inline=True)
            embed.add_field(name="Open Positions", value=f"`{stats['open_positions']}`", inline=False)
            
            embed.set_footer(text=settings.bot_name)
            await ctx.send(embed=embed)

        @self.command(name="pause", help="Pause the trading bot execution")
        async def cmd_pause(ctx):
            self.orchestrator.pause()
            await self.send_system_embed("⏸️ Bot Paused", "Trading execution has been suspended.", 0xe74c3c)

        @self.command(name="resume", help="Resume the trading bot execution")
        async def cmd_resume(ctx):
            self.orchestrator.resume()
            await self.send_system_embed("▶️ Bot Resumed", "Trading execution has been resumed.", 0x2ecc71)

        @self.command(name="stats", help="Show trading stats and win rate metrics")
        async def cmd_stats(ctx):
            summary = self.orchestrator.performance_tracker.get_summary()
            embed = discord.Embed(
                title="📈 Bot Performance Statistics",
                color=0x9b59b6,
                timestamp=datetime.now()
            )
            embed.add_field(name="Total Trades", value=f"`{summary['total_trades']}`", inline=True)
            embed.add_field(name="Win Rate (Rolling)", value=f"**{summary['win_rate']*100:.1f}%**", inline=True)
            embed.add_field(name="Profit Factor", value=f"`{summary['profit_factor']}`", inline=True)
            embed.add_field(name="Average R:R", value=f"`{summary['average_rr']}`", inline=True)
            embed.add_field(name="Total P&L", value=f"**${summary['total_pnl']:.2f}**", inline=True)
            embed.add_field(name="Max Drawdown", value=f"`{summary['max_drawdown']*100:.2f}%`", inline=True)
            
            embed.set_footer(text=settings.bot_name)
            await ctx.send(embed=embed)

        @self.command(name="risk", help="Show current risk parameters and limits")
        async def cmd_risk(ctx):
            risk_stats = await self.orchestrator.risk_manager.get_risk_stats()
            embed = discord.Embed(
                title="🛡️ Risk Management Summary",
                color=0xe74c3c,
                timestamp=datetime.now()
            )
            embed.add_field(name="Open Positions", value=f"`{risk_stats['open_positions']}/{risk_stats['max_positions']}`", inline=True)
            embed.add_field(name="Daily Drawdown", value=f"`{risk_stats['daily_drawdown_pct']}% / {risk_stats['daily_drawdown_limit']}%`", inline=True)
            embed.add_field(name="Consecutive Losses", value=f"`{risk_stats['consecutive_losses']}`", inline=True)
            embed.add_field(name="Cooldown Active", value="🔴 YES" if risk_stats["cooldown_active"] else "🟢 NO", inline=True)
            if risk_stats["cooldown_until"]:
                embed.add_field(name="Cooldown Until", value=risk_stats["cooldown_until"], inline=False)
                
            embed.set_footer(text=settings.bot_name)
            await ctx.send(embed=embed)

        @self.command(name="pairs", help="Show active trading pairs")
        async def cmd_pairs(ctx):
            pairs_str = "\n".join([f"- **{p}**" for p in settings.trading_pairs])
            embed = discord.Embed(
                title="💱 Active Trading Pairs",
                description=pairs_str,
                color=0x3498db
            )
            embed.set_footer(text=settings.bot_name)
            await ctx.send(embed=embed)

        @self.command(name="release_regime", help="Release a forced regime override so the bot auto-detects again")
        async def cmd_release_regime(ctx):
            self.orchestrator.release_forced_regime()
            await self.send_system_embed("🔓 Forced Regime Released", "Auto-detection resumed.", 0x3498db)

        # ── Track (d) — Emergency-flatten via Discord. ──
        # Magic-number isolation is enforced inside orchestrator.close_all_trades;
        # foreign / manual trades are never touched. ``!flatten`` is a
        # terser alias because ``!close_all`` is awkward to type on mobile.
        @self.command(name="close_all", aliases=["flatten"], help="Force-close all Genesis positions immediately (magic-filtered)")
        async def cmd_close_all(ctx):
            self.orchestrator.pause()
            await ctx.send("⚠️ Closing all Genesis positions...")
            closed = await self.orchestrator.close_all_trades()
            if closed:
                await ctx.send(
                    f"🛑 Closed {len(closed)} Genesis position(s). Bot is PAUSED."
                )
            else:
                await ctx.send(
                    "ℹ️ No Genesis positions were open. Bot remains PAUSED — !resume to restart."
                )

        @self.command(name="kill_switch", help="Inspect kill-switch state. Optional: `!kill_switch release` to clear + resume.")
        async def cmd_kill_switch(ctx, action: str = None):
            """Track (d): report tripped kill switches. ``release`` arg
            clears all underlying trip flags AND the orchestrator's
            engagement latch AND un-pauses the bot."""
            rm = self.orchestrator.risk_manager
            orch = self.orchestrator
            trip = rm.tripped_kill_switches()
            trip_active = any(trip.values()) or orch._kill_switch_fired
            embed = discord.Embed(
                title="🛡️ Kill-Switch State",
                color=0xe74c3c if trip_active else 0x2ecc71,
                timestamp=datetime.now(),
            )
            embed.add_field(
                name="Daily Drawdown",
                value="🔴 TRIPPED" if trip["daily_drawdown"] else "🟢 clear",
                inline=True,
            )
            embed.add_field(
                name="Equity Floor",
                value="🔴 TRIPPED" if trip["equity_floor"] else "🟢 clear",
                inline=True,
            )
            embed.add_field(
                name="Engagement Latch",
                value=(
                    "🔴 ACTIVE — emergency flatten was triggered this trip"
                    if orch._kill_switch_fired
                    else "🟢 clear"
                ),
                inline=False,
            )
            embed.add_field(
                name="Bot Paused",
                value=("yes" if orch._paused else "no"),
                inline=True,
            )
            embed.set_footer(text=settings.bot_name)
            await ctx.send(embed=embed)

            if action and action.lower() == "release":
                rm.release_daily_dd_trip()
                rm.release_equity_floor_trip()
                cleared = orch.release_kill_switch_engagement()
                if cleared:
                    await self.send_system_embed(
                        "🟢 Kill switches released",
                        "Underlying trip flags + engagement latch cleared. "
                        "Bot is RESUMING — inspect state before relying on it.",
                        0x2ecc71,
                    )
                    orch.resume()
                else:
                    await self.send_system_embed(
                        "ℹ️ Nothing to release",
                        "No kill switch was engaged — underlying trip flags still cleared.",
                        0x3498db,
                    )

        @self.command(name="trades", help="Show the last 5 trades")
        async def cmd_trades(ctx):
            recent_trades = self.orchestrator.performance_tracker._get_window(5)
            
            if not recent_trades:
                await ctx.send("ℹ️ No trades recorded yet.")
                return
                
            embed = discord.Embed(
                title="📋 Recent Completed Trades",
                color=0x3498db,
                timestamp=datetime.now()
            )
            for t in recent_trades:
                win_status = "🟢" if t["is_win"] else "🔴"
                embed.add_field(
                    name=f"{win_status} {t['symbol']} {t['direction'].upper()}",
                    value=f"Ticket: `{t['ticket']}` | P&L: **${t['profit']:.2f}** | R:R: `{t.get('achieved_rr', 0)}` | Strategy: *{t.get('strategy')}*",
                    inline=False
                )
            embed.set_footer(text=settings.bot_name)
            await ctx.send(embed=embed)
