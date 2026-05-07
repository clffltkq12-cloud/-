import os
import time
import math
from dataclasses import dataclass
from datetime import datetime, timezone

import ccxt
import numpy as np
import pandas as pd
from dotenv import load_dotenv


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = np.where(delta > 0, delta, 0.0)
    down = np.where(delta < 0, -delta, 0.0)
    up_ema = pd.Series(up, index=series.index).ewm(alpha=1 / period, adjust=False).mean()
    down_ema = pd.Series(down, index=series.index).ewm(alpha=1 / period, adjust=False).mean()
    rs = up_ema / down_ema.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


@dataclass
class Config:
    exchange: str
    api_key: str
    api_secret: str
    symbol: str
    dry_run: bool
    mode: str
    risk_per_trade: float
    daily_max_loss: float
    max_trades_per_day: int
    max_consecutive_losses: int
    leverage: int
    stop_loss_pct: float
    take_profit_1r: float
    take_profit_2r: float
    partial_tp_ratio: float
    loop_seconds: int


class TrendPullbackBot:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.ex = self._make_exchange(cfg)
        self.day = datetime.now(timezone.utc).date()
        self.day_pnl = 0.0
        self.day_trades = 0
        self.consecutive_losses = 0
        self.position = None

    def _make_exchange(self, cfg: Config):
        if cfg.exchange == "binance":
            ex = ccxt.binanceusdm({"apiKey": cfg.api_key, "secret": cfg.api_secret, "enableRateLimit": True})
            if cfg.api_key and cfg.api_secret:
                try:
                    ex.set_leverage(cfg.leverage, cfg.symbol)
                except Exception as e:
                    print(f"[WARN] leverage set failed: {e}")
            return ex
        if cfg.exchange == "upbit":
            return ccxt.upbit({"apiKey": cfg.api_key, "secret": cfg.api_secret, "enableRateLimit": True})
        raise ValueError("EXCHANGE must be binance or upbit")

    def fetch_df(self, timeframe: str, limit: int = 300):
        ohlcv = self.ex.fetch_ohlcv(self.cfg.symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df["ema20"] = ema(df["close"], 20)
        df["ema50"] = ema(df["close"], 50)
        df["ema200"] = ema(df["close"], 200)
        df["rsi"] = rsi(df["close"], 14)
        return df

    def trend_direction(self, h1: pd.DataFrame):
        row = h1.iloc[-1]
        score_up = 0
        score_dn = 0
        if row.close > row.ema200:
            score_up += 1
        else:
            score_dn += 1
        if row.ema50 > row.ema200:
            score_up += 1
        else:
            score_dn += 1
        if h1.close.iloc[-1] > h1.close.iloc[-4]:
            score_up += 1
        else:
            score_dn += 1
        if score_up >= 2:
            return "up"
        if score_dn >= 2:
            return "down"
        return "flat"

    def entry_signal(self, m5: pd.DataFrame, trend: str):
        c = m5.iloc[-1]
        p = m5.iloc[-2]
        long_score = 0
        short_score = 0

        if c.close >= c.ema20 * 0.998 and c.close <= c.ema50 * 1.002:
            long_score += 1
        if c.rsi >= 40 and c.rsi <= 55 and c.rsi > p.rsi:
            long_score += 1
        if c.close > c.open:
            long_score += 1

        if c.close <= c.ema20 * 1.002 and c.close >= c.ema50 * 0.998:
            short_score += 1
        if c.rsi <= 60 and c.rsi >= 45 and c.rsi < p.rsi:
            short_score += 1
        if c.close < c.open:
            short_score += 1

        if trend == "up" and long_score >= 2:
            return "long"
        if trend == "down" and self.cfg.mode == "BOTH" and self.cfg.exchange == "binance" and short_score >= 2:
            return "short"
        return None

    def fetch_balance_usd(self):
        bal = self.ex.fetch_balance()
        if self.cfg.exchange == "upbit":
            krw = bal.get("KRW", {}).get("free", 0)
            return float(krw)
        usdt = bal.get("USDT", {}).get("free", 0)
        return float(usdt)

    def size_by_risk(self, entry_price: float, stop_price: float):
        equity = self.fetch_balance_usd()
        risk_amount = equity * self.cfg.risk_per_trade
        stop_distance = abs(entry_price - stop_price) / entry_price
        if stop_distance <= 0:
            return 0
        notional = risk_amount / stop_distance
        qty = notional / entry_price
        return max(qty, 0)

    def place_entry(self, side: str, entry: float, stop: float):
        qty = self.size_by_risk(entry, stop)
        if qty <= 0:
            print("[INFO] qty=0; skip")
            return
        print(f"[ENTRY] {side} qty={qty:.6f} entry={entry:.2f} stop={stop:.2f}")
        if self.cfg.dry_run:
            self.position = {"side": side, "qty": qty, "entry": entry, "stop": stop, "tp1_done": False}
            self.day_trades += 1
            return

        order_side = "buy" if side == "long" else "sell"
        self.ex.create_limit_order(self.cfg.symbol, order_side, qty, entry)
        self.position = {"side": side, "qty": qty, "entry": entry, "stop": stop, "tp1_done": False}
        self.day_trades += 1

    def manage_position(self, price: float):
        if not self.position:
            return
        pos = self.position
        side = pos["side"]
        entry = pos["entry"]
        stop = pos["stop"]
        risk = abs(entry - stop)

        if side == "long":
            tp1 = entry + risk * self.cfg.take_profit_1r
            tp2 = entry + risk * self.cfg.take_profit_2r
            hit_stop = price <= stop
            hit_tp1 = price >= tp1
            hit_tp2 = price >= tp2
        else:
            tp1 = entry - risk * self.cfg.take_profit_1r
            tp2 = entry - risk * self.cfg.take_profit_2r
            hit_stop = price >= stop
            hit_tp1 = price <= tp1
            hit_tp2 = price <= tp2

        if hit_stop:
            pnl = -self.cfg.risk_per_trade
            self.day_pnl += pnl
            self.consecutive_losses += 1
            print(f"[EXIT-STOP] pnl={pnl:.4f}")
            self.position = None
            return

        if hit_tp1 and not pos["tp1_done"]:
            pos["tp1_done"] = True
            pos["stop"] = entry
            print("[TP1] partial + move stop to breakeven")

        if hit_tp2:
            pnl = self.cfg.risk_per_trade * self.cfg.take_profit_2r
            self.day_pnl += pnl
            self.consecutive_losses = 0
            print(f"[EXIT-TP2] pnl={pnl:.4f}")
            self.position = None

    def guardrails_ok(self):
        if datetime.now(timezone.utc).date() != self.day:
            self.day = datetime.now(timezone.utc).date()
            self.day_pnl = 0
            self.day_trades = 0
            self.consecutive_losses = 0

        if self.day_trades >= self.cfg.max_trades_per_day:
            return False, "max trades/day reached"
        if self.day_pnl <= -self.cfg.daily_max_loss:
            return False, "daily max loss reached"
        if self.consecutive_losses >= self.cfg.max_consecutive_losses:
            return False, "max consecutive losses reached"
        return True, "ok"

    def run_once(self):
        ok, reason = self.guardrails_ok()
        if not ok:
            print(f"[PAUSE] {reason}")
            return

        h1 = self.fetch_df("1h")
        m5 = self.fetch_df("5m")
        trend = self.trend_direction(h1)
        last_price = float(m5.close.iloc[-1])

        self.manage_position(last_price)
        if self.position is not None:
            return

        signal = self.entry_signal(m5, trend)
        if signal is None:
            print(f"[NO-TRADE] trend={trend}")
            return

        sl_pct = self.cfg.stop_loss_pct
        stop = last_price * (1 - sl_pct) if signal == "long" else last_price * (1 + sl_pct)
        self.place_entry(signal, last_price, stop)


def load_config() -> Config:
    load_dotenv()
    return Config(
        exchange=os.getenv("EXCHANGE", "binance").lower(),
        api_key=os.getenv("API_KEY", ""),
        api_secret=os.getenv("API_SECRET", ""),
        symbol=os.getenv("SYMBOL", "BTC/USDT:USDT"),
        dry_run=os.getenv("DRY_RUN", "true").lower() == "true",
        mode=os.getenv("MODE", "LONG_ONLY").upper(),
        risk_per_trade=float(os.getenv("RISK_PER_TRADE", "0.01")),
        daily_max_loss=float(os.getenv("DAILY_MAX_LOSS", "0.02")),
        max_trades_per_day=int(os.getenv("MAX_TRADES_PER_DAY", "3")),
        max_consecutive_losses=int(os.getenv("MAX_CONSECUTIVE_LOSSES", "2")),
        leverage=int(os.getenv("LEVERAGE", "3")),
        stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "0.006")),
        take_profit_1r=float(os.getenv("TAKE_PROFIT_1R", "1.0")),
        take_profit_2r=float(os.getenv("TAKE_PROFIT_2R", "1.7")),
        partial_tp_ratio=float(os.getenv("PARTIAL_TP_RATIO", "0.5")),
        loop_seconds=int(os.getenv("LOOP_SECONDS", "300")),
    )


if __name__ == "__main__":
    cfg = load_config()
    bot = TrendPullbackBot(cfg)
    print(f"[START] exchange={cfg.exchange} symbol={cfg.symbol} dry_run={cfg.dry_run} mode={cfg.mode}")

    while True:
        try:
            bot.run_once()
        except Exception as e:
            print(f"[ERROR] {e}")
        time.sleep(cfg.loop_seconds)
