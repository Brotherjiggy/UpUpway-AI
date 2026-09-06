import os
import time
import asyncio
import logging
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# ============================================================
# UPUPWAY AI
# VERSION 1.5.0
# Intelligent Paper Trading Backend
# ============================================================

APP_NAME = "Upupway AI"
VERSION = "1.5.0"
BUILD_ID = "2026-09-05-v1.5.0"
MODE = "paper"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("upupway-ai")


# ============================================================
# ENVIRONMENT
# ============================================================

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
COINGECKO_TIMEOUT = 15

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv(
    "SUPABASE_SERVICE_ROLE_KEY",
    ""
)


# ============================================================
# SETTINGS
# ============================================================

MARKET_CACHE_TTL = 300.0
PROVIDER_MIN_INTERVAL = 120.0
STALE_MARKET_MAX_AGE = 86400.0

COLLECTOR_INTERVAL_SECONDS = 60

LOCAL_HISTORY_MAX_POINTS = 500

# More history is required for reliable analysis.
LOCAL_HISTORY_REQUIRED_POINTS = 30

RSI_PERIOD = 14
SHORT_MA_PERIOD = 5
LONG_MA_PERIOD = 14

# Minimum percentage movement required to consider
# the history meaningfully different.
MIN_HISTORY_MOVEMENT_PERCENT = 0.05

PAPER_ACCOUNT_KEY = "demo"
BOT_STATE_KEY = "main"

RISK_SETTINGS = {
    "max_position_percent": 10.0,
    "minimum_confidence": 55.0,
    "trade_cooldown_seconds": 60.0,
    "stop_loss_percent": 3.0,
    "take_profit_percent": 6.0,
    "daily_loss_limit_percent": 5.0,
    "max_consecutive_losses": 3,
}


# ============================================================
# IN-MEMORY CACHE
# ============================================================

market_cache = None
market_cache_time = 0.0
last_provider_request = 0.0

btc_history = deque(
    maxlen=LOCAL_HISTORY_MAX_POINTS
)

collector_running = False
collector_task = None


# ============================================================
# FALLBACK MARKET DATA
# ============================================================

last_known_market = None
last_known_market_time = 0.0


# ============================================================
# SUPABASE HELPERS
# ============================================================

def supabase_configured():
    return bool(
        SUPABASE_URL and
        SUPABASE_SERVICE_ROLE_KEY
    )


def supabase_headers():
    if not supabase_configured():
        raise RuntimeError(
            "Supabase environment variables are missing."
        )

    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": (
            f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"
        ),
        "Content-Type": "application/json",
    }


def supabase_request(
    method: str,
    table: str,
    params=None,
    payload=None,
):
    """
    Generic Supabase REST request.
    """

    if not supabase_configured():
        raise RuntimeError(
            "Supabase is not configured."
        )

    url = (
        f"{SUPABASE_URL}/rest/v1/{table}"
    )

    headers = supabase_headers()

    if method.upper() in [
        "POST",
        "PATCH",
        "DELETE",
    ]:
        headers["Prefer"] = "return=representation"

    response = requests.request(
        method=method.upper(),
        url=url,
        headers=headers,
        params=params,
        json=payload,
        timeout=15,
    )

    if not response.ok:
        raise RuntimeError(
            f"Supabase {method.upper()} {table} failed: "
            f"{response.status_code} {response.text}"
        )

    if not response.text:
        return []

    return response.json()


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def parse_timestamp(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00"
            )
        )
    except Exception:
        return None


# ============================================================
# SUPABASE — PAPER ACCOUNT
# ============================================================

def default_paper_account():

    return {
        "account_key": PAPER_ACCOUNT_KEY,
        "starting_balance": 10000.0,
        "cash": 10000.0,
        "btc": 0.0,
        "entry_price": None,
        "last_action": "NONE",
        "profit_loss": 0.0,
        "realized_profit_loss": 0.0,
        "unrealized_profit_loss": 0.0,
        "portfolio_value": 10000.0,
        "updated_at": utc_now(),
    }


def load_paper_account():

    try:

        rows = supabase_request(
            "GET",
            "paper_accounts",
            params={
                "account_key": (
                    f"eq.{PAPER_ACCOUNT_KEY}"
                ),
                "limit": "1",
            },
        )

        if rows:

            account = rows[0]

            for key in [
                "starting_balance",
                "cash",
                "btc",
                "entry_price",
                "profit_loss",
                "realized_profit_loss",
                "unrealized_profit_loss",
                "portfolio_value",
            ]:

                if account.get(key) is not None:
                    account[key] = float(
                        account[key]
                    )

            return account

        account = default_paper_account()

        supabase_request(
            "POST",
            "paper_accounts",
            payload=account,
        )

        return account

    except Exception as exc:

        logger.warning(
            "Could not load paper account: %s",
            exc,
        )

        return default_paper_account()


def save_paper_account(account):

    try:

        account["updated_at"] = utc_now()

        payload = {
            "starting_balance": account[
                "starting_balance"
            ],
            "cash": account["cash"],
            "btc": account["btc"],
            "entry_price": account[
                "entry_price"
            ],
            "last_action": account[
                "last_action"
            ],
            "profit_loss": account[
                "profit_loss"
            ],
            "realized_profit_loss": account[
                "realized_profit_loss"
            ],
            "unrealized_profit_loss": account[
                "unrealized_profit_loss"
            ],
            "portfolio_value": account[
                "portfolio_value"
            ],
            "updated_at": account[
                "updated_at"
            ],
        }

        supabase_request(
            "PATCH",
            "paper_accounts",
            params={
                "account_key": (
                    f"eq.{PAPER_ACCOUNT_KEY}"
                )
            },
            payload=payload,
        )

    except Exception as exc:

        logger.warning(
            "Could not save paper account: %s",
            exc,
        )


paper_account = load_paper_account()


# ============================================================
# SUPABASE — BOT STATE
# ============================================================

def default_bot_state():

    return {
        "state_key": BOT_STATE_KEY,
        "enabled": False,
        "last_signal": "NONE",
        "last_action": "NONE",
        "last_price": None,
        "last_trade_time": None,
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "consecutive_losses": 0,
        "updated_at": utc_now(),
    }


def load_bot_state():

    try:

        rows = supabase_request(
            "GET",
            "bot_state",
            params={
                "state_key": (
                    f"eq.{BOT_STATE_KEY}"
                ),
                "limit": "1",
            },
        )

        if rows:

            state = rows[0]

            state.setdefault(
                "consecutive_losses",
                0
            )

            return state

        state = default_bot_state()

        supabase_request(
            "POST",
            "bot_state",
            payload=state,
        )

        return state

    except Exception as exc:

        logger.warning(
            "Could not load bot state: %s",
            exc,
        )

        return default_bot_state()


auto_trading = load_bot_state()


def save_bot_state():

    try:

        auto_trading["updated_at"] = utc_now()

        payload = {
            "enabled": bool(
                auto_trading["enabled"]
            ),
            "last_signal": auto_trading[
                "last_signal"
            ],
            "last_action": auto_trading[
                "last_action"
            ],
            "last_price": auto_trading[
                "last_price"
            ],
            "last_trade_time": auto_trading[
                "last_trade_time"
            ],
            "trades": auto_trading[
                "trades"
            ],
            "wins": auto_trading[
                "wins"
            ],
            "losses": auto_trading[
                "losses"
            ],
            "updated_at": auto_trading[
                "updated_at"
            ],
        }

        # Only send consecutive_losses if the
        # database column exists in the future.
        # The core bot remains compatible with
        # the current v1.4 schema.

        supabase_request(
            "PATCH",
            "bot_state",
            params={
                "state_key": (
                    f"eq.{BOT_STATE_KEY}"
                )
            },
            payload=payload,
        )

    except Exception as exc:

        logger.warning(
            "Could not save bot state: %s",
            exc,
        )


# ============================================================
# SUPABASE — TRADES
# ============================================================

def save_trade(
    side,
    price,
    quantity,
    amount,
    reason,
    profit_loss=0.0,
    source="manual",
):

    trade = {
        "account_key": PAPER_ACCOUNT_KEY,
        "timestamp": utc_now(),
        "side": side,
        "symbol": "BTCUSDT",
        "price": float(price),
        "quantity": float(quantity),
        "amount": float(amount),
        "reason": reason,
        "profit_loss": float(profit_loss),
        "source": source,
        "paper_only": True,
    }

    try:

        supabase_request(
            "POST",
            "trades",
            payload=trade,
        )

    except Exception as exc:

        logger.warning(
            "Could not save trade: %s",
            exc,
        )


def get_trades(limit=100):

    try:

        return supabase_request(
            "GET",
            "trades",
            params={
                "account_key": (
                    f"eq.{PAPER_ACCOUNT_KEY}"
                ),
                "order": "timestamp.desc",
                "limit": str(limit),
            },
        )

    except Exception as exc:

        logger.warning(
            "Could not load trades: %s",
            exc,
        )

        return []


# ============================================================
# SUPABASE — PRICE SNAPSHOTS
# ============================================================

def save_price_snapshot(price):

    try:

        supabase_request(
            "POST",
            "price_snapshots",
            payload={
                "symbol": "BTCUSDT",
                "price": float(price),
                "captured_at": utc_now(),
            },
        )

    except Exception as exc:

        logger.warning(
            "Could not save price snapshot: %s",
            exc,
        )


def load_price_history():

    try:

        rows = supabase_request(
            "GET",
            "price_snapshots",
            params={
                "symbol": "eq.BTCUSDT",
                "order": "captured_at.desc",
                "limit": str(
                    LOCAL_HISTORY_MAX_POINTS
                ),
            },
        )

        prices = []

        for row in reversed(rows):

            try:
                prices.append(
                    float(row["price"])
                )
            except Exception:
                continue

        btc_history.clear()
        btc_history.extend(prices)

        logger.info(
            "Loaded %s BTC price snapshots "
            "from Supabase.",
            len(btc_history),
        )

    except Exception as exc:

        logger.warning(
            "Could not load price history: %s",
            exc,
        )


# ============================================================
# SUPABASE — SIGNALS
# ============================================================

def save_signal(signal):

    payload = {
        "timestamp": signal.get(
            "generated_at",
            utc_now(),
        ),
        "symbol": "BTCUSDT",
        "action": signal.get(
            "action",
            "HOLD",
        ),
        "confidence": float(
            signal.get(
                "confidence",
                0,
            )
        ),
        "trend": signal.get(
            "trend",
            "NEUTRAL",
        ),
        "rsi": signal.get("rsi"),
        "price": float(
            signal.get(
                "price",
                0,
            )
        ),
        "short_ma": signal.get(
            "short_ma"
        ),
        "long_ma": signal.get(
            "long_ma"
        ),
        "momentum": signal.get(
            "momentum"
        ),
        "price_vs_ma": signal.get(
            "price_vs_ma"
        ),
        "data_quality": signal.get(
            "data_quality"
        ),
        "score": signal.get(
            "score"
        ),
        "description": signal.get(
            "description"
        ),
        "history_points": signal.get(
            "history_points"
        ),
        "source": signal.get(
            "source",
            "UpUpway AI",
        ),
        "paper_only": True,
    }

    try:

        supabase_request(
            "POST",
            "signals",
            payload=payload,
        )

    except Exception as exc:

        logger.warning(
            "Could not save signal: %s",
            exc,
        )


def get_signals(limit=100):

    try:

        return supabase_request(
            "GET",
            "signals",
            params={
                "symbol": "eq.BTCUSDT",
                "order": "timestamp.desc",
                "limit": str(limit),
            },
        )

    except Exception as exc:

        logger.warning(
            "Could not load signals: %s",
            exc,
        )

        return []


# ============================================================
# MARKET DATA
# ============================================================

def market_from_supabase():

    try:

        rows = supabase_request(
            "GET",
            "price_snapshots",
            params={
                "symbol": "eq.BTCUSDT",
                "order": "captured_at.desc",
                "limit": "1",
            },
        )

        if not rows:
            return None

        price = float(
            rows[0]["price"]
        )

        return {
            "symbol": "BTCUSDT",
            "price": price,
            "change_24h": None,
            "volume_24h": None,
            "source": "Supabase last known price",
            "eth_price": None,
            "eth_change_24h": None,
            "sol_price": None,
            "sol_change_24h": None,
            "updated_at": rows[0].get(
                "captured_at",
                utc_now(),
            ),
            "stale": True,
        }

    except Exception as exc:

        logger.warning(
            "Could not retrieve fallback price: %s",
            exc,
        )

        return None


def fetch_coingecko_market():

    global last_provider_request

    now = time.time()

    if (
        last_provider_request > 0
        and now - last_provider_request
        < PROVIDER_MIN_INTERVAL
    ):

        raise RuntimeError(
            "CoinGecko provider cooldown active."
        )

    last_provider_request = now

    url = (
        f"{COINGECKO_BASE_URL}/simple/price"
    )

    params = {
        "ids": (
            "bitcoin,ethereum,solana"
        ),
        "vs_currencies": "usd",
        "include_24hr_change": "true",
    }

    response = requests.get(
        url,
        params=params,
        timeout=COINGECKO_TIMEOUT,
    )

    if response.status_code == 429:

        raise RuntimeError(
            "CoinGecko rate limit reached (429)."
        )

    response.raise_for_status()

    data = response.json()

    btc = data.get(
        "bitcoin",
        {}
    )

    eth = data.get(
        "ethereum",
        {}
    )

    sol = data.get(
        "solana",
        {}
    )

    if not btc.get("usd"):

        raise RuntimeError(
            "CoinGecko returned no BTC price."
        )

    return {
        "symbol": "BTCUSDT",
        "price": float(
            btc["usd"]
        ),
        "change_24h": btc.get(
            "usd_24h_change"
        ),
        "volume_24h": None,
        "source": "CoinGecko",
        "eth_price": eth.get("usd"),
        "eth_change_24h": eth.get(
            "usd_24h_change"
        ),
        "sol_price": sol.get("usd"),
        "sol_change_24h": sol.get(
            "usd_24h_change"
        ),
        "updated_at": utc_now(),
        "stale": False,
    }


def get_market_data():

    global market_cache
    global market_cache_time
    global last_known_market
    global last_known_market_time

    now = time.time()

    # --------------------------------------------------------
    # Fresh cache
    # --------------------------------------------------------

    if (
        market_cache is not None
        and now - market_cache_time
        < MARKET_CACHE_TTL
    ):

        return market_cache

    # --------------------------------------------------------
    # CoinGecko
    # --------------------------------------------------------

    try:

        market = fetch_coingecko_market()

        market_cache = market
        market_cache_time = now

        last_known_market = market
        last_known_market_time = now

        save_price_snapshot(
            market["price"]
        )

        return market

    except Exception as exc:

        logger.warning(
            "CoinGecko unavailable: %s",
            exc,
        )

        # ----------------------------------------------------
        # Memory fallback
        # ----------------------------------------------------

        if (
            last_known_market is not None
            and now - last_known_market_time
            <= STALE_MARKET_MAX_AGE
        ):

            fallback = dict(
                last_known_market
            )

            fallback["source"] = (
                "Cached market data"
            )

            fallback["stale"] = True

            return fallback

        # ----------------------------------------------------
        # Supabase fallback
        # ----------------------------------------------------

        persistent = market_from_supabase()

        if persistent:
            return persistent

        raise HTTPException(
            status_code=503,
            detail=(
                "Market data provider unavailable "
                "and no cached market price "
                "is available."
            ),
        )


# ============================================================
# LOCAL HISTORY
# ============================================================

def record_local_price(price):

    try:

        price = float(price)

        # Avoid adding the exact same snapshot
        # repeatedly when the market has not changed.
        if btc_history:

            previous = btc_history[-1]

            if previous == price:

                logger.info(
                    "Duplicate BTC price ignored: %.2f",
                    price,
                )

                return

        btc_history.append(price)

    except Exception:
        pass


# ============================================================
# TECHNICAL INDICATORS
# ============================================================

def calculate_rsi(
    prices,
    period=RSI_PERIOD,
):

    if len(prices) <= period:
        return None

    gains = []
    losses = []

    for i in range(
        1,
        len(prices),
    ):

        change = (
            prices[i] -
            prices[i - 1]
        )

        if change > 0:

            gains.append(change)
            losses.append(0.0)

        elif change < 0:

            gains.append(0.0)
            losses.append(
                abs(change)
            )

        else:

            gains.append(0.0)
            losses.append(0.0)

    recent_gains = gains[
        -period:
    ]

    recent_losses = losses[
        -period:
    ]

    average_gain = (
        sum(recent_gains) /
        period
    )

    average_loss = (
        sum(recent_losses) /
        period
    )

    # If there are no gains and no losses,
    # there is no meaningful RSI.
    if (
        average_gain == 0
        and average_loss == 0
    ):

        return 50.0

    if average_loss == 0:

        return 100.0

    rs = (
        average_gain /
        average_loss
    )

    return 100.0 - (
        100.0 /
        (1.0 + rs)
    )


def calculate_sma(
    prices,
    period,
):

    if len(prices) < period:
        return None

    return (
        sum(prices[-period:]) /
        period
    )


# ============================================================
# DATA QUALITY
# ============================================================

def assess_data_quality(prices):

    count = len(prices)

    if count < LOCAL_HISTORY_REQUIRED_POINTS:

        return {
            "quality": "POOR",
            "reason": (
                "Insufficient price history."
            ),
            "movement_percent": 0.0,
        }

    recent_window = prices[
        -LOCAL_HISTORY_REQUIRED_POINTS:
    ]

    highest = max(
        recent_window
    )

    lowest = min(
        recent_window
    )

    latest = recent_window[-1]

    if latest <= 0:

        return {
            "quality": "POOR",
            "reason": "Invalid BTC price.",
            "movement_percent": 0.0,
        }

    movement_percent = (
        (highest - lowest) /
        latest *
        100
    )

    unique_prices = len(
        set(
            round(
                p,
                8
            )
            for p in recent_window
        )
    )

    if unique_prices <= 2:

        return {
            "quality": "POOR",
            "reason": (
                "Price history contains "
                "almost no variation."
            ),
            "movement_percent": round(
                movement_percent,
                4,
            ),
        }

    if (
        movement_percent
        < MIN_HISTORY_MOVEMENT_PERCENT
    ):

        return {
            "quality": "LIMITED",
            "reason": (
                "Market movement is very small."
            ),
            "movement_percent": round(
                movement_percent,
                4,
            ),
        }

    return {
        "quality": "GOOD",
        "reason": (
            "Sufficient history and "
            "meaningful price variation."
        ),
        "movement_percent": round(
            movement_percent,
            4,
        ),
    }


# ============================================================
# MOMENTUM
# ============================================================

def calculate_momentum(prices):

    if len(prices) < 10:

        return {
            "label": "UNKNOWN",
            "percent": 0.0,
        }

    current = prices[-1]
    previous = prices[-10]

    if previous <= 0:

        return {
            "label": "UNKNOWN",
            "percent": 0.0,
        }

    percent = (
        (current - previous) /
        previous *
        100
    )

    if percent >= 0.5:

        label = "STRONG"

    elif percent >= 0.15:

        label = "POSITIVE"

    elif percent <= -0.5:

        label = "STRONG"

    elif percent <= -0.15:

        label = "NEGATIVE"

    else:

        label = "WEAK"

    return {
        "label": label,
        "percent": round(
            percent,
            4,
        ),
    }


# ============================================================
# SIGNAL ENGINE
# ============================================================

def build_hold_signal(
    price,
    description,
    confidence=0.0,
    trend="NEUTRAL",
    rsi=None,
    short_ma=None,
    long_ma=None,
    momentum="UNKNOWN",
    price_vs_ma="UNKNOWN",
    data_quality="POOR",
    score=50.0,
):

    signal = {
        "action": "HOLD",
        "description": description,
        "confidence": float(
            max(
                0,
                min(
                    100,
                    confidence
                )
            )
        ),
        "trend": trend,
        "rsi": (
            round(rsi, 2)
            if rsi is not None
            else None
        ),
        "price": float(price),
        "short_ma": (
            round(short_ma, 2)
            if short_ma is not None
            else None
        ),
        "long_ma": (
            round(long_ma, 2)
            if long_ma is not None
            else None
        ),
        "momentum": momentum,
        "price_vs_ma": price_vs_ma,
        "data_quality": data_quality,
        "score": float(score),
        "source": (
            "UpUpway AI v1.5.0"
        ),
        "paper_only": True,
        "history_points": len(
            btc_history
        ),
        "generated_at": utc_now(),
    }

    return signal


def generate_signal():

    if not btc_history:

        market = get_market_data()

        signal = build_hold_signal(
            price=market["price"],
            description=(
                "No local market history "
                "is available yet."
            ),
            confidence=0.0,
            trend="WARMING_UP",
            data_quality="POOR",
            score=50.0,
        )

        save_signal(signal)

        return signal

    prices = list(
        btc_history
    )

    price = prices[-1]

    quality = assess_data_quality(
        prices
    )

    # --------------------------------------------------------
    # Insufficient history
    # --------------------------------------------------------

    if len(prices) < LOCAL_HISTORY_REQUIRED_POINTS:

        signal = build_hold_signal(
            price=price,
            description=(
                "AI signal engine is warming up. "
                "Collecting persistent market history."
            ),
            confidence=0.0,
            trend="WARMING_UP",
            data_quality=quality[
                "quality"
            ],
            score=50.0,
        )

        save_signal(signal)

        return signal

    # --------------------------------------------------------
    # Poor data quality
    # --------------------------------------------------------

    if quality["quality"] == "POOR":

        signal = build_hold_signal(
            price=price,
            description=(
                "Data quality is too poor for "
                "a reliable trading signal. "
                "The engine will HOLD."
            ),
            confidence=5.0,
            trend="NEUTRAL",
            data_quality="POOR",
            score=50.0,
        )

        save_signal(signal)

        return signal

    # --------------------------------------------------------
    # Indicators
    # --------------------------------------------------------

    rsi = calculate_rsi(
        prices,
        RSI_PERIOD,
    )

    short_ma = calculate_sma(
        prices,
        SHORT_MA_PERIOD,
    )

    long_ma = calculate_sma(
        prices,
        LONG_MA_PERIOD,
    )

    momentum = calculate_momentum(
        prices
    )

    if (
        rsi is None
        or short_ma is None
        or long_ma is None
    ):

        signal = build_hold_signal(
            price=price,
            description=(
                "Technical indicators are "
                "not yet sufficiently established."
            ),
            confidence=10.0,
            trend="UNKNOWN",
            rsi=rsi,
            short_ma=short_ma,
            long_ma=long_ma,
            momentum=momentum[
                "label"
            ],
            data_quality=quality[
                "quality"
            ],
        )

        save_signal(signal)

        return signal

    # --------------------------------------------------------
    # Price position
    # --------------------------------------------------------

    ma_difference_percent = (
        (price - long_ma) /
        long_ma *
        100
    )

    if ma_difference_percent > 0.15:

        price_vs_ma = "ABOVE"

    elif ma_difference_percent < -0.15:

        price_vs_ma = "BELOW"

    else:

        price_vs_ma = "NEUTRAL"

    # --------------------------------------------------------
    # Trend
    # --------------------------------------------------------

    if (
        short_ma > long_ma
        and price > long_ma
    ):

        trend = "BULLISH"

    elif (
        short_ma < long_ma
        and price < long_ma
    ):

        trend = "BEARISH"

    else:

        trend = "NEUTRAL"

    # --------------------------------------------------------
    # Independent scoring
    #
    # Score starts at neutral 50.
    #
    # RSI
    # MA relationship
    # Price position
    # Momentum
    #
    # No single indicator can force a trade.
    # --------------------------------------------------------

    score = 50.0

    reasons = []

    # --------------------------------------------------------
    # RSI contribution
    # --------------------------------------------------------

    # Extremely high RSI is NOT automatically SELL.
    # It only contributes bearish evidence.
    if rsi < 30:

        score += 15

        reasons.append(
            "RSI indicates oversold conditions."
        )

    elif rsi < 40:

        score += 7

        reasons.append(
            "RSI is moderately weak."
        )

    elif rsi > 70:

        score -= 15

        reasons.append(
            "RSI indicates overbought conditions."
        )

    elif rsi > 60:

        score -= 7

        reasons.append(
            "RSI is moderately elevated."
        )

    else:

        reasons.append(
            "RSI is in a neutral range."
        )

    # --------------------------------------------------------
    # Moving average contribution
    # --------------------------------------------------------

    if short_ma > long_ma:

        score += 20

        reasons.append(
            "Short MA is above long MA."
        )

    elif short_ma < long_ma:

        score -= 20

        reasons.append(
            "Short MA is below long MA."
        )

    else:

        reasons.append(
            "Moving averages are aligned."
        )

    # --------------------------------------------------------
    # Price vs MA
    # --------------------------------------------------------

    if price_vs_ma == "ABOVE":

        score += 10

        reasons.append(
            "Price is above the long MA."
        )

    elif price_vs_ma == "BELOW":

        score -= 10

        reasons.append(
            "Price is below the long MA."
        )

    else:

        reasons.append(
            "Price is near the long MA."
        )

    # --------------------------------------------------------
    # Momentum
    # --------------------------------------------------------

    momentum_percent = momentum[
        "percent"
    ]

    if momentum_percent >= 0.5:

        score += 10

        reasons.append(
            "Momentum is strongly positive."
        )

    elif momentum_percent >= 0.15:

        score += 5

        reasons.append(
            "Momentum is positive."
        )

    elif momentum_percent <= -0.5:

        score -= 10

        reasons.append(
            "Momentum is strongly negative."
        )

    elif momentum_percent <= -0.15:

        score -= 5

        reasons.append(
            "Momentum is negative."
        )

    else:

        reasons.append(
            "Momentum is weak."
        )

    score = max(
        0,
        min(
            100,
            score
        )
    )

    # --------------------------------------------------------
    # Determine action
    # --------------------------------------------------------

    if score >= 70:

        action = "BUY"

    elif score <= 30:

        action = "SELL"

    else:

        action = "HOLD"

    # --------------------------------------------------------
    # Conflict protection
    #
    # If RSI and trend strongly disagree,
    # downgrade the action to HOLD.
    # --------------------------------------------------------

    conflict = False

    if (
        trend == "BULLISH"
        and rsi > 75
        and momentum_percent < 0
    ):

        conflict = True

    if (
        trend == "BEARISH"
        and rsi < 25
        and momentum_percent > 0
    ):

        conflict = True

    if conflict:

        action = "HOLD"

        reasons.append(
            "Indicators conflict, so the engine "
            "is protecting the account with HOLD."
        )

    # --------------------------------------------------------
    # Confidence
    # --------------------------------------------------------

    distance_from_neutral = abs(
        score - 50
    )

    confidence = (
        45 +
        distance_from_neutral * 1.1
    )

    # Data quality adjustment
    if quality["quality"] == "LIMITED":

        confidence -= 10

    confidence = max(
        0,
        min(
            95,
            confidence
        )
    )

    # A HOLD can still have useful confidence,
    # but confidence means confidence in the
    # current market assessment, not guaranteed profit.
    if action == "HOLD":

        confidence = min(
            confidence,
            69
        )

    if conflict:

        confidence = min(
            confidence,
            55
        )

    description = (
        " | ".join(reasons)
    )

    signal = {
        "action": action,
        "description": description,
        "confidence": round(
            confidence,
            2,
        ),
        "trend": trend,
        "rsi": round(
            rsi,
            2,
        ),
        "price": float(price),
        "short_ma": round(
            short_ma,
            2,
        ),
        "long_ma": round(
            long_ma,
            2,
        ),
        "momentum": (
            momentum["label"]
        ),
        "momentum_percent": (
            momentum["percent"]
        ),
        "price_vs_ma": price_vs_ma,
        "data_quality": quality[
            "quality"
        ],
        "data_movement_percent": quality[
            "movement_percent"
        ],
        "score": round(
            score,
            2,
        ),
        "source": (
            "UpUpway AI v1.5.0"
        ),
        "paper_only": True,
        "history_points": len(
            btc_history
        ),
        "generated_at": utc_now(),
    }

    save_signal(signal)

    return signal


# ============================================================
# PORTFOLIO
# ============================================================

def update_portfolio_value(price):

    global paper_account

    btc_value = (
        paper_account["btc"] *
        price
    )

    portfolio_value = (
        paper_account["cash"] +
        btc_value
    )

    unrealized = 0.0

    if (
        paper_account["btc"] > 0
        and paper_account["entry_price"]
    ):

        unrealized = (
            price -
            paper_account["entry_price"]
        ) * paper_account["btc"]

    paper_account[
        "unrealized_profit_loss"
    ] = unrealized

    paper_account[
        "portfolio_value"
    ] = portfolio_value

    paper_account[
        "profit_loss"
    ] = (
        portfolio_value -
        paper_account[
            "starting_balance"
        ]
    )

    save_paper_account(
        paper_account
    )


# ============================================================
# PAPER BUY
# ============================================================

def execute_paper_buy(
    source="manual",
    reason="Manual paper buy",
):

    market = get_market_data()

    price = float(
        market["price"]
    )

    allocation = (
        paper_account["cash"] *
        RISK_SETTINGS[
            "max_position_percent"
        ] /
        100
    )

    if allocation <= 0:

        raise HTTPException(
            status_code=400,
            detail=(
                "Insufficient paper cash."
            ),
        )

    quantity = (
        allocation /
        price
    )

    paper_account["cash"] -= (
        allocation
    )

    previous_btc = (
        paper_account["btc"]
    )

    if (
        previous_btc > 0
        and paper_account[
            "entry_price"
        ]
    ):

        total_cost = (
            previous_btc *
            paper_account[
                "entry_price"
            ]
        ) + allocation

        new_quantity = (
            previous_btc +
            quantity
        )

        paper_account[
            "entry_price"
        ] = (
            total_cost /
            new_quantity
        )

    else:

        paper_account[
            "entry_price"
        ] = price

    paper_account["btc"] += (
        quantity
    )

    paper_account[
        "last_action"
    ] = "BUY"

    update_portfolio_value(
        price
    )

    save_trade(
        side="BUY",
        price=price,
        quantity=quantity,
        amount=allocation,
        reason=reason,
        profit_loss=0,
        source=source,
    )

    auto_trading[
        "last_action"
    ] = "BUY"

    auto_trading[
        "last_price"
    ] = price

    if source == "auto":

        auto_trading[
            "trades"
        ] += 1

    save_bot_state()

    return {
        "success": True,
        "action": "BUY",
        "price": price,
        "quantity": quantity,
        "amount": allocation,
        "source": source,
        "paper_only": True,
        "account": paper_account,
    }


# ============================================================
# PAPER SELL
# ============================================================

def execute_paper_sell(
    source="manual",
    reason="Manual paper sell",
):

    market = get_market_data()

    price = float(
        market["price"]
    )

    quantity = (
        paper_account["btc"]
    )

    if quantity <= 0:

        raise HTTPException(
            status_code=400,
            detail=(
                "No BTC position to sell."
            ),
        )

    amount = (
        quantity *
        price
    )

    entry_price = (
        paper_account[
            "entry_price"
        ] or price
    )

    pnl = (
        price -
        entry_price
    ) * quantity

    paper_account[
        "cash"
    ] += amount

    paper_account[
        "btc"
    ] = 0.0

    paper_account[
        "entry_price"
    ] = None

    paper_account[
        "last_action"
    ] = "SELL"

    paper_account[
        "realized_profit_loss"
    ] += pnl

    update_portfolio_value(
        price
    )

    save_trade(
        side="SELL",
        price=price,
        quantity=quantity,
        amount=amount,
        reason=reason,
        profit_loss=pnl,
        source=source,
    )

    auto_trading[
        "last_action"
    ] = "SELL"

    auto_trading[
        "last_price"
    ] = price

    if source == "auto":

        auto_trading[
            "trades"
        ] += 1

    if pnl > 0:

        auto_trading[
            "wins"
        ] += 1

        auto_trading[
            "consecutive_losses"
        ] = 0

    elif pnl < 0:

        auto_trading[
            "losses"
        ] += 1

        auto_trading[
            "consecutive_losses"
        ] = (
            auto_trading.get(
                "consecutive_losses",
                0
            ) + 1
        )

    save_bot_state()

    return {
        "success": True,
        "action": "SELL",
        "price": price,
        "quantity": quantity,
        "amount": amount,
        "profit_loss": pnl,
        "source": source,
        "paper_only": True,
        "account": paper_account,
    }


# ============================================================
# RISK ENGINE
# ============================================================

def cooldown_active():

    last_trade = auto_trading.get(
        "last_trade_time"
    )

    if not last_trade:

        return False

    timestamp = parse_timestamp(
        last_trade
    )

    if not timestamp:

        return False

    elapsed = (
        datetime.now(
            timezone.utc
        ) -
        timestamp
    ).total_seconds()

    return (
        elapsed <
        RISK_SETTINGS[
            "trade_cooldown_seconds"
        ]
    )


def risk_check():

    portfolio_value = float(
        paper_account[
            "portfolio_value"
        ]
    )

    starting_balance = float(
        paper_account[
            "starting_balance"
        ]
    )

    if starting_balance <= 0:

        return {
            "allowed": False,
            "reason": (
                "Invalid starting balance."
            ),
        }

    # --------------------------------------------------------
    # Current portfolio drawdown
    # --------------------------------------------------------

    drawdown = (
        starting_balance -
        portfolio_value
    ) / starting_balance * 100

    if drawdown >= RISK_SETTINGS[
        "daily_loss_limit_percent"
    ]:

        return {
            "allowed": False,
            "reason": (
                "Loss protection limit reached."
            ),
            "drawdown_percent": round(
                drawdown,
                2,
            ),
        }

    # --------------------------------------------------------
    # Consecutive losses
    # --------------------------------------------------------

    consecutive_losses = auto_trading.get(
        "consecutive_losses",
        0
    )

    if (
        consecutive_losses >=
        RISK_SETTINGS[
            "max_consecutive_losses"
        ]
    ):

        return {
            "allowed": False,
            "reason": (
                "Consecutive loss protection active."
            ),
            "consecutive_losses": (
                consecutive_losses
            ),
        }

    # --------------------------------------------------------
    # Cooldown
    # --------------------------------------------------------

    if cooldown_active():

        return {
            "allowed": False,
            "reason": (
                "Trading cooldown active."
            ),
        }

    return {
        "allowed": True,
        "reason": (
            "Risk checks passed."
        ),
        "drawdown_percent": round(
            drawdown,
            2,
        ),
        "consecutive_losses": (
            consecutive_losses
        ),
    }


# ============================================================
# AUTO TRADING
# ============================================================

def run_auto_trading():

    if not auto_trading[
        "enabled"
    ]:

        return {
            "success": False,
            "message": (
                "Auto trading is disabled."
            ),
            "paper_only": True,
        }

    # --------------------------------------------------------
    # Market
    # --------------------------------------------------------

    market = get_market_data()

    price = float(
        market["price"]
    )

    record_local_price(
        price
    )

    update_portfolio_value(
        price
    )

    # --------------------------------------------------------
    # Existing position management
    # --------------------------------------------------------

    if (
        paper_account["btc"] > 0
        and paper_account[
            "entry_price"
        ]
    ):

        entry = float(
            paper_account[
                "entry_price"
            ]
        )

        change_percent = (
            (price - entry) /
            entry *
            100
        )

        if change_percent <= -RISK_SETTINGS[
            "stop_loss_percent"
        ]:

            result = execute_paper_sell(
                source="auto",
                reason=(
                    "Stop loss triggered"
                ),
            )

            auto_trading[
                "last_trade_time"
            ] = utc_now()

            save_bot_state()

            return result

        if change_percent >= RISK_SETTINGS[
            "take_profit_percent"
        ]:

            result = execute_paper_sell(
                source="auto",
                reason=(
                    "Take profit triggered"
                ),
            )

            auto_trading[
                "last_trade_time"
            ] = utc_now()

            save_bot_state()

            return result

    # --------------------------------------------------------
    # Risk check BEFORE discretionary signal trade
    # --------------------------------------------------------

    risk = risk_check()

    if not risk["allowed"]:

        return {
            "success": False,
            "message": risk["reason"],
            "risk_blocked": True,
            "risk": risk,
            "paper_only": True,
        }

    # --------------------------------------------------------
    # Signal
    # --------------------------------------------------------

    signal = generate_signal()

    auto_trading[
        "last_signal"
    ] = signal["action"]

    auto_trading[
        "last_price"
    ] = price

    confidence = float(
        signal.get(
            "confidence",
            0,
        )
    )

    # --------------------------------------------------------
    # Data quality protection
    # --------------------------------------------------------

    if signal.get(
        "data_quality"
    ) != "GOOD":

        save_bot_state()

        return {
            "success": True,
            "action": "HOLD",
            "reason": (
                "Trading blocked because "
                "market data quality is "
                "not GOOD."
            ),
            "signal": signal,
            "paper_only": True,
        }

    # --------------------------------------------------------
    # Confidence protection
    # --------------------------------------------------------

    if confidence < RISK_SETTINGS[
        "minimum_confidence"
    ]:

        save_bot_state()

        return {
            "success": True,
            "action": "HOLD",
            "reason": (
                "Signal confidence below "
                "minimum threshold."
            ),
            "signal": signal,
            "paper_only": True,
        }

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    if (
        signal["action"] == "BUY"
        and paper_account[
            "btc"
        ] <= 0
    ):

        result = execute_paper_buy(
            source="auto",
            reason=(
                "AI BUY signal"
            ),
        )

        auto_trading[
            "last_trade_time"
        ] = utc_now()

        save_bot_state()

        return result

    # --------------------------------------------------------
    # SELL
    # --------------------------------------------------------

    if (
        signal["action"] == "SELL"
        and paper_account[
            "btc"
        ] > 0
    ):

        result = execute_paper_sell(
            source="auto",
            reason=(
                "AI SELL signal"
            ),
        )

        auto_trading[
            "last_trade_time"
        ] = utc_now()

        save_bot_state()

        return result

    # --------------------------------------------------------
    # HOLD
    # --------------------------------------------------------

    save_bot_state()

    return {
        "success": True,
        "action": "HOLD",
        "reason": (
            "No trade conditions met."
        ),
        "signal": signal,
        "paper_only": True,
    }


# ============================================================
# RESET
# ============================================================

def reset_everything():

    global paper_account
    global auto_trading

    paper_account = (
        default_paper_account()
    )

    auto_trading = (
        default_bot_state()
    )

    try:

        supabase_request(
            "PATCH",
            "paper_accounts",
            params={
                "account_key": (
                    f"eq.{PAPER_ACCOUNT_KEY}"
                )
            },
            payload=paper_account,
        )

        supabase_request(
            "PATCH",
            "bot_state",
            params={
                "state_key": (
                    f"eq.{BOT_STATE_KEY}"
                )
            },
            payload=auto_trading,
        )

        supabase_request(
            "DELETE",
            "trades",
            params={
                "account_key": (
                    f"eq.{PAPER_ACCOUNT_KEY}"
                )
            },
        )

        supabase_request(
            "DELETE",
            "signals",
            params={
                "symbol": "eq.BTCUSDT"
            },
        )

        return True

    except Exception as exc:

        logger.warning(
            "Reset persistence error: %s",
            exc,
        )

        return False


# ============================================================
# BACKTEST DATA
# ============================================================

def historical_btc_prices(
    days=30
):

    url = (
        f"{COINGECKO_BASE_URL}/coins/"
        f"bitcoin/market_chart"
    )

    params = {
        "vs_currency": "usd",
        "days": days,
    }

    response = requests.get(
        url,
        params=params,
        timeout=COINGECKO_TIMEOUT,
    )

    if response.status_code == 429:

        raise RuntimeError(
            "CoinGecko rate limit reached "
            "during backtest."
        )

    response.raise_for_status()

    data = response.json()

    return [
        float(item[1])
        for item in data.get(
            "prices",
            []
        )
    ]


# ============================================================
# BACKTEST
# ============================================================

def calculate_backtest_signal(
    prices
):

    quality = assess_data_quality(
        prices
    )

    if (
        len(prices)
        < LOCAL_HISTORY_REQUIRED_POINTS
    ):

        return "HOLD", 0

    if quality["quality"] == "POOR":

        return "HOLD", 0

    rsi = calculate_rsi(
        prices,
        RSI_PERIOD,
    )

    short_ma = calculate_sma(
        prices,
        SHORT_MA_PERIOD,
    )

    long_ma = calculate_sma(
        prices,
        LONG_MA_PERIOD,
    )

    momentum = calculate_momentum(
        prices
    )

    if (
        rsi is None
        or short_ma is None
        or long_ma is None
    ):

        return "HOLD", 0

    price = prices[-1]

    score = 50.0

    if rsi < 30:
        score += 15

    elif rsi < 40:
        score += 7

    elif rsi > 70:
        score -= 15

    elif rsi > 60:
        score -= 7

    if short_ma > long_ma:
        score += 20

    elif short_ma < long_ma:
        score -= 20

    if price > long_ma:
        score += 10

    elif price < long_ma:
        score -= 10

    if momentum["percent"] >= 0.5:
        score += 10

    elif momentum["percent"] >= 0.15:
        score += 5

    elif momentum["percent"] <= -0.5:
        score -= 10

    elif momentum["percent"] <= -0.15:
        score -= 5

    score = max(
        0,
        min(
            100,
            score
        )
    )

    if score >= 70:
        return "BUY", score

    if score <= 30:
        return "SELL", score

    return "HOLD", score


def run_backtest(
    days=30
):

    try:

        prices = historical_btc_prices(
            days
        )

    except Exception as exc:

        raise HTTPException(
            status_code=503,
            detail=(
                f"Backtest data unavailable: "
                f"{exc}"
            ),
        )

    if len(prices) < 20:

        raise HTTPException(
            status_code=400,
            detail=(
                "Not enough historical data."
            ),
        )

    starting_cash = 10000.0

    cash = starting_cash
    btc = 0.0

    buy_count = 0
    sell_count = 0

    for i in range(
        LOCAL_HISTORY_REQUIRED_POINTS,
        len(prices)
    ):

        window = prices[
            :i + 1
        ]

        signal, score = (
            calculate_backtest_signal(
                window
            )
        )

        price = prices[i]

        if (
            signal == "BUY"
            and btc <= 0
        ):

            allocation = (
                cash * 0.10
            )

            if allocation > 0:

                btc += (
                    allocation /
                    price
                )

                cash -= allocation

                buy_count += 1

        elif (
            signal == "SELL"
            and btc > 0
        ):

            cash += (
                btc * price
            )

            btc = 0.0

            sell_count += 1

    final_price = prices[-1]

    final_value = (
        cash +
        btc * final_price
    )

    strategy_return = (
        (
            final_value -
            starting_cash
        ) /
        starting_cash *
        100
    )

    buy_hold_btc = (
        starting_cash /
        prices[0]
    )

    buy_hold_value = (
        buy_hold_btc *
        final_price
    )

    buy_hold_return = (
        (
            buy_hold_value -
            starting_cash
        ) /
        starting_cash *
        100
    )

    return {
        "success": True,
        "version": VERSION,
        "days": days,
        "starting_balance": (
            starting_cash
        ),
        "ending_balance": round(
            final_value,
            2,
        ),
        "strategy_return_percent": round(
            strategy_return,
            2,
        ),
        "buy_and_hold_return_percent": round(
            buy_hold_return,
            2,
        ),
        "difference_vs_buy_and_hold": round(
            strategy_return -
            buy_hold_return,
            2,
        ),
        "data_points": len(
            prices
        ),
        "buy_signals": buy_count,
        "sell_signals": sell_count,
        "paper_only": True,
    }


# ============================================================
# API MODELS
# ============================================================

class AutoTradingRequest(
    BaseModel
):

    enabled: bool


# ============================================================
# FASTAPI LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app):

    global collector_task
    global collector_running

    logger.info(
        "Starting %s v%s",
        APP_NAME,
        VERSION,
    )

    await asyncio.to_thread(
        load_price_history
    )

    collector_running = True

    collector_task = (
        asyncio.create_task(
            market_history_collector()
        )
    )

    yield

    collector_running = False

    if collector_task:

        collector_task.cancel()

        try:

            await collector_task

        except asyncio.CancelledError:

            pass

    logger.info(
        "Upupway AI shutdown complete."
    )


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(
    title="UpUpway AI",
    version=VERSION,
    description=(
        "Intelligent AI crypto "
        "paper-trading backend."
    ),
    lifespan=lifespan,
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# BACKGROUND COLLECTOR
# ============================================================

async def market_history_collector():

    logger.info(
        "Market history collector started. "
        "Interval=%ss",
        COLLECTOR_INTERVAL_SECONDS,
    )

    while collector_running:

        try:

            market = await asyncio.to_thread(
                get_market_data
            )

            price = market.get(
                "price"
            )

            if price:

                record_local_price(
                    price
                )

                logger.info(
                    "BTC snapshot recorded: "
                    "%.2f | history=%s",
                    price,
                    len(btc_history),
                )

        except Exception as exc:

            logger.warning(
                "History collector warning: %s",
                exc,
            )

        await asyncio.sleep(
            COLLECTOR_INTERVAL_SECONDS
        )


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():

    return {
        "name": APP_NAME,
        "status": "online",
        "mode": MODE,
        "version": VERSION,
        "build_id": BUILD_ID,
        "message": (
            "Upupway AI trading backend "
            "is running."
        ),
        "paper_trading": True,
        "real_money_trading": False,
        "supabase_persistence": (
            supabase_configured()
        ),
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/api/health")
def health():

    return {
        "status": "healthy",
        "service": APP_NAME,
        "version": VERSION,
        "mode": MODE,
        "paper_only": True,
        "supabase_configured": (
            supabase_configured()
        ),
        "history_points": len(
            btc_history
        ),
        "collector_running": (
            collector_running
        ),
        "timestamp": utc_now(),
    }


# ============================================================
# STATUS
# ============================================================

@app.get("/api/status")
def status():

    return {
        "name": APP_NAME,
        "version": VERSION,
        "build_id": BUILD_ID,
        "status": "online",
        "mode": MODE,
        "market_data": (
            "CoinGecko + persistent fallback"
        ),
        "signal_engine": (
            "RSI + MA + Momentum + "
            "Data Quality"
        ),
        "auto_trading": (
            auto_trading["enabled"]
        ),
        "paper_trading": True,
        "real_money_trading": False,
        "api_keys_required": False,
        "supabase_persistence": (
            supabase_configured()
        ),
        "history_points": len(
            btc_history
        ),
        "collector_running": (
            collector_running
        ),
        "collector_interval_seconds": (
            COLLECTOR_INTERVAL_SECONDS
        ),
    }


# ============================================================
# MARKET
# ============================================================

@app.get("/api/market")
def market():

    return get_market_data()


# ============================================================
# SIGNAL
# ============================================================

@app.get("/api/signal")
def signal():

    market_data = get_market_data()

    record_local_price(
        market_data["price"]
    )

    return generate_signal()


# ============================================================
# SIGNAL HISTORY
# ============================================================

@app.get("/api/signals")
def signals(limit: int = 100):

    if limit < 1:
        limit = 1

    if limit > 500:
        limit = 500

    rows = get_signals(
        limit
    )

    return {
        "success": True,
        "count": len(rows),
        "signals": rows,
        "paper_only": True,
    }


# ============================================================
# HISTORY STATUS
# ============================================================

@app.get("/api/history-status")
def history_status():

    quality = assess_data_quality(
        list(btc_history)
    )

    return {
        "success": True,
        "history_points": len(
            btc_history
        ),
        "required_points": (
            LOCAL_HISTORY_REQUIRED_POINTS
        ),
        "ready": (
            len(btc_history) >=
            LOCAL_HISTORY_REQUIRED_POINTS
        ),
        "max_points": (
            LOCAL_HISTORY_MAX_POINTS
        ),
        "data_quality": quality[
            "quality"
        ],
        "movement_percent": quality[
            "movement_percent"
        ],
        "quality_reason": quality[
            "reason"
        ],
        "source": (
            "Supabase persistent snapshots"
        ),
        "collector_running": (
            collector_running
        ),
        "collector_interval_seconds": (
            COLLECTOR_INTERVAL_SECONDS
        ),
        "supabase_persistence": (
            supabase_configured()
        ),
        "paper_only": True,
    }


# ============================================================
# PAPER ACCOUNT
# ============================================================

@app.get("/api/paper-account")
def get_paper_account():

    market_data = get_market_data()

    update_portfolio_value(
        market_data["price"]
    )

    return {
        **paper_account,
        "btc_price": (
            market_data["price"]
        ),
        "paper_only": True,
    }


# ============================================================
# PAPER BUY
# ============================================================

@app.post("/api/paper-buy")
def paper_buy():

    return execute_paper_buy()


# ============================================================
# PAPER SELL
# ============================================================

@app.post("/api/paper-sell")
def paper_sell():

    return execute_paper_sell()


# ============================================================
# TRADES
# ============================================================

@app.get("/api/trades")
def trades():

    rows = get_trades()

    return {
        "success": True,
        "count": len(rows),
        "trades": rows,
        "paper_only": True,
    }


# ============================================================
# RISK SETTINGS
# ============================================================

@app.get("/api/risk-settings")
def risk_settings():

    return {
        "success": True,
        "risk_settings": RISK_SETTINGS,
        "paper_only": True,
    }


@app.get("/api/risk")
def risk():

    return {
        "success": True,
        **risk_check(),
        "risk_settings": RISK_SETTINGS,
        "paper_only": True,
    }


# ============================================================
# AUTO TRADING
# ============================================================

@app.get("/api/auto-trading")
def get_auto_trading():

    return {
        "success": True,
        **auto_trading,
        "risk_settings": RISK_SETTINGS,
        "cooldown_active": (
            cooldown_active()
        ),
        "paper_only": True,
    }


@app.post(
    "/api/auto-trading/toggle"
)
def toggle_auto_trading(
    request: AutoTradingRequest
):

    auto_trading[
        "enabled"
    ] = request.enabled

    if not request.enabled:

        auto_trading[
            "last_action"
        ] = "OFF"

    save_bot_state()

    return {
        "success": True,
        "enabled": (
            auto_trading["enabled"]
        ),
        "paper_only": True,
        "message": (
            "Auto trading enabled."
            if request.enabled
            else "Auto trading disabled."
        ),
    }


@app.post(
    "/api/auto-trading/run"
)
def auto_trading_run():

    return run_auto_trading()


# ============================================================
# RESET
# ============================================================

@app.post(
    "/api/paper-account/reset"
)
def paper_account_reset():

    success = reset_everything()

    btc_history.clear()

    return {
        "success": True,
        "persistence_reset": success,
        "account": paper_account,
        "auto_trading": auto_trading,
        "paper_only": True,
    }


# ============================================================
# BACKTEST
# ============================================================

@app.post("/api/backtest")
def backtest(days: int = 30):

    if days < 1:
        days = 1

    if days > 365:
        days = 365

    return run_backtest(
        days
    )


# ============================================================
# RUN LOCALLY
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8000"
            )
        ),
        )
