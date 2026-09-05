from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from collections import deque
from datetime import datetime, timezone
from contextlib import asynccontextmanager
import asyncio
import requests
import time
import statistics
import uuid


# ============================================================
# UPUPWAY AI
# Backend v1.3.1
# ============================================================

APP_NAME = "Upupway AI"
VERSION = "1.3.1"
BUILD_ID = "2026-09-05-v1.3.1"

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
REQUEST_TIMEOUT = 15

# Market protection
MARKET_CACHE_TTL = 300.0
PROVIDER_MIN_INTERVAL = 120.0
STALE_MARKET_MAX_AGE = 86400.0

# Historical/backtest protection
HISTORY_CACHE_TTL = 900.0
HISTORY_PROVIDER_MIN_INTERVAL = 600.0

# Local live-history collector
COLLECTOR_INTERVAL_SECONDS = 60
LOCAL_HISTORY_MAX_POINTS = 500
LOCAL_HISTORY_REQUIRED_POINTS = 20


# ============================================================
# GLOBAL MARKET STATE
# ============================================================

market_cache: Optional[Dict[str, Any]] = None
market_cache_time = 0.0
last_market_provider_request = 0.0

historical_cache: Dict[int, Dict[str, Any]] = {}
last_history_provider_request: Dict[int, float] = {}

local_price_history = deque(
    maxlen=LOCAL_HISTORY_MAX_POINTS
)

collector_task = None


# ============================================================
# PAPER ACCOUNT
# ============================================================

paper_account = {
    "starting_balance": 10000.0,
    "cash": 10000.0,
    "btc": 0.0,
    "entry_price": None,
    "last_action": "NONE",
    "profit_loss": 0.0,
    "realized_profit_loss": 0.0,
    "unrealized_profit_loss": 0.0,
    "portfolio_value": 10000.0,
}


# ============================================================
# RISK SETTINGS
# ============================================================

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
# AUTO TRADING
# ============================================================

auto_trading = {
    "enabled": False,
    "last_signal": "NONE",
    "last_action": "NONE",
    "last_price": None,
    "last_trade_time": None,
    "trades": 0,
    "wins": 0,
    "losses": 0,
}


trade_history: List[Dict[str, Any]] = []


# ============================================================
# FASTAPI LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    global collector_task

    print("=" * 60)
    print(f"{APP_NAME} backend starting")
    print(f"Version: {VERSION}")
    print(f"Build: {BUILD_ID}")
    print("Mode: PAPER TRADING")
    print("Real-money trading: DISABLED")
    print("Market provider: CoinGecko")
    print("Signal engine: RSI + MA + Local History")
    print(
        f"Live collector interval: "
        f"{COLLECTOR_INTERVAL_SECONDS}s"
    )
    print("=" * 60)

    collector_task = asyncio.create_task(
        market_history_collector()
    )

    yield

    if collector_task:

        collector_task.cancel()

        try:
            await collector_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description="Upupway AI paper-trading backend.",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# HELPERS
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clamp(
    value: float,
    minimum: float,
    maximum: float
) -> float:

    return max(
        minimum,
        min(value, maximum)
    )


def safe_float(
    value,
    default=None
):

    try:
        return float(value)

    except (TypeError, ValueError):
        return default


# ============================================================
# LOCAL PRICE HISTORY
# ============================================================

def record_local_price(
    price: float,
    timestamp: Optional[float] = None
):

    if price is None or price <= 0:
        return

    if timestamp is None:
        timestamp = time.time()

    # Avoid duplicate timestamps.
    if local_price_history:

        last = local_price_history[-1]

        if abs(
            timestamp - last["timestamp"]
        ) < 1:

            last["price"] = price

            return

    local_price_history.append({
        "timestamp": timestamp,
        "price": price,
    })


# ============================================================
# MARKET PROVIDER
# ============================================================

def fetch_market_from_provider():

    global last_market_provider_request

    url = (
        f"{COINGECKO_BASE_URL}"
        "/simple/price"
    )

    params = {
        "ids": "bitcoin,ethereum,solana",
        "vs_currencies": "usd",
        "include_24hr_change": "true",
    }

    response = requests.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    data = response.json()

    bitcoin = data.get(
        "bitcoin",
        {}
    )

    ethereum = data.get(
        "ethereum",
        {}
    )

    solana = data.get(
        "solana",
        {}
    )

    btc_price = safe_float(
        bitcoin.get("usd")
    )

    if btc_price is None:
        raise RuntimeError(
            "Bitcoin price was not returned."
        )

    market = {
        "symbol": "BTCUSDT",

        "price": btc_price,

        "change_24h": safe_float(
            bitcoin.get("usd_24h_change"),
            0.0,
        ),

        "volume_24h": None,

        "eth_price": safe_float(
            ethereum.get("usd")
        ),

        "eth_change_24h": safe_float(
            ethereum.get(
                "usd_24h_change"
            ),
            0.0,
        ),

        "sol_price": safe_float(
            solana.get("usd")
        ),

        "sol_change_24h": safe_float(
            solana.get(
                "usd_24h_change"
            ),
            0.0,
        ),

        "source": "CoinGecko",

        "updated_at": now_iso(),
    }

    last_market_provider_request = time.time()

    return market


# ============================================================
# MARKET DATA
# ============================================================

def get_market_data():

    global market_cache
    global market_cache_time

    current_time = time.time()

    # --------------------------------------------------------
    # Fresh cache
    # --------------------------------------------------------

    if market_cache is not None:

        age = (
            current_time
            - market_cache_time
        )

        if age < MARKET_CACHE_TTL:

            market = dict(
                market_cache
            )

            market["cache"] = "fresh"

            market["cache_age_seconds"] = round(
                age,
                2
            )

            record_local_price(
                market["price"]
            )

            return market

    # --------------------------------------------------------
    # Provider cooldown
    # --------------------------------------------------------

    provider_age = (
        current_time
        - last_market_provider_request
    )

    if (
        market_cache is not None
        and provider_age
        < PROVIDER_MIN_INTERVAL
    ):

        market = dict(
            market_cache
        )

        market["cache"] = "cooldown"

        market["cache_age_seconds"] = round(
            current_time
            - market_cache_time,
            2
        )

        record_local_price(
            market["price"]
        )

        return market

    # --------------------------------------------------------
    # Provider refresh
    # --------------------------------------------------------

    try:

        market = fetch_market_from_provider()

        market_cache = dict(
            market
        )

        market_cache_time = current_time

        market["cache"] = "provider"

        record_local_price(
            market["price"]
        )

        return market

    except Exception as exc:

        # ----------------------------------------------------
        # Stale fallback
        # ----------------------------------------------------

        if market_cache is not None:

            stale_age = (
                current_time
                - market_cache_time
            )

            if (
                stale_age
                <= STALE_MARKET_MAX_AGE
            ):

                market = dict(
                    market_cache
                )

                market["cache"] = (
                    "stale_fallback"
                )

                market[
                    "cache_age_seconds"
                ] = round(
                    stale_age,
                    2
                )

                market["warning"] = (
                    "CoinGecko temporarily "
                    "unavailable. Using cached "
                    "market data."
                )

                record_local_price(
                    market["price"]
                )

                return market

        raise HTTPException(
            status_code=503,
            detail=(
                "Market data provider "
                f"unavailable: {exc}"
            ),
        )


# ============================================================
# AUTOMATIC LOCAL HISTORY COLLECTOR
# ============================================================

async def market_history_collector():

    print(
        "Upupway AI local market "
        "history collector started."
    )

    while True:

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

                print(
                    "History snapshot:",
                    price,
                    "| points:",
                    len(
                        local_price_history
                    )
                )

        except Exception as exc:

            print(
                "History collector warning:",
                exc
            )

        await asyncio.sleep(
            COLLECTOR_INTERVAL_SECONDS
        )


# ============================================================
# HISTORICAL DATA FOR BACKTESTING
# ============================================================

def get_historical_prices(
    days: int = 30
):

    global historical_cache
    global last_history_provider_request

    days = max(
        1,
        min(days, 365)
    )

    current_time = time.time()

    cached = historical_cache.get(
        days
    )

    if cached:

        age = (
            current_time
            - cached["timestamp"]
        )

        if age < HISTORY_CACHE_TTL:

            return cached["prices"]

    last_request = (
        last_history_provider_request
        .get(days, 0)
    )

    if (
        current_time
        - last_request
        < HISTORY_PROVIDER_MIN_INTERVAL
    ):

        if cached:
            return cached["prices"]

        raise HTTPException(
            status_code=503,
            detail=(
                "Historical data provider "
                "is temporarily on cooldown."
            ),
        )

    url = (
        f"{COINGECKO_BASE_URL}"
        "/coins/bitcoin/market_chart"
    )

    params = {
        "vs_currency": "usd",
        "days": days,
    }

    try:

        response = requests.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

        raw_prices = data.get(
            "prices",
            []
        )

        prices = []

        for item in raw_prices:

            if len(item) >= 2:

                price = safe_float(
                    item[1]
                )

                if (
                    price is not None
                    and price > 0
                ):

                    prices.append(
                        price
                    )

        if len(prices) < 20:

            raise RuntimeError(
                "Not enough historical "
                "Bitcoin prices returned."
            )

        historical_cache[days] = {
            "timestamp": current_time,
            "prices": prices,
        }

        last_history_provider_request[
            days
        ] = current_time

        return prices

    except Exception as exc:

        if cached:
            return cached["prices"]

        raise HTTPException(
            status_code=503,
            detail=(
                "Historical data unavailable: "
                f"{exc}"
            ),
        )


# ============================================================
# TECHNICAL INDICATORS
# ============================================================

def calculate_sma(
    prices: List[float],
    period: int
):

    if len(prices) < period:
        return None

    return statistics.mean(
        prices[-period:]
    )


def calculate_rsi(
    prices: List[float],
    period: int = 14
):

    if len(prices) <= period:
        return None

    changes = []

    for index in range(
        1,
        len(prices)
    ):

        changes.append(
            prices[index]
            - prices[index - 1]
        )

    recent_changes = changes[
        -period:
    ]

    gains = [
        change
        for change in recent_changes
        if change > 0
    ]

    losses = [
        abs(change)
        for change in recent_changes
        if change < 0
    ]

    average_gain = (
        sum(gains) / period
        if gains
        else 0.0
    )

    average_loss = (
        sum(losses) / period
        if losses
        else 0.0
    )

    if average_loss == 0:

        if average_gain == 0:
            return 50.0

        return 100.0

    rs = (
        average_gain
        / average_loss
    )

    rsi = (
        100
        - (
            100
            / (1 + rs)
        )
    )

    return round(
        clamp(rsi, 0, 100),
        2
    )


# ============================================================
# SIGNAL ENGINE
# ============================================================

def generate_signal():

    market = get_market_data()

    price = market["price"]

    prices = [
        item["price"]
        for item in local_price_history
    ]

    history_count = len(prices)

    # --------------------------------------------------------
    # WARMING UP
    # --------------------------------------------------------

    if history_count < LOCAL_HISTORY_REQUIRED_POINTS:

        change_24h = safe_float(
            market.get(
                "change_24h"
            ),
            0.0
        )

        if change_24h > 2:
            trend = "BULLISH"

        elif change_24h < -2:
            trend = "BEARISH"

        else:
            trend = "NEUTRAL"

        return {
            "action": "HOLD",

            "description": (
                "Upupway AI is collecting "
                "local BTC price history "
                "before enabling full "
                "technical analysis."
            ),

            "confidence": 50.0,

            "trend": trend,

            "rsi": None,

            "price": price,

            "short_ma": None,

            "long_ma": None,

            "source": (
                "Upupway AI v1.3.1 "
                "local signal engine"
            ),

            "analysis_status": "WARMING_UP",

            "history_points": history_count,

            "required_history_points":
                LOCAL_HISTORY_REQUIRED_POINTS,

            "paper_only": True,

            "generated_at": now_iso(),
        }

    # --------------------------------------------------------
    # INDICATORS
    # --------------------------------------------------------

    rsi = calculate_rsi(
        prices,
        14
    )

    short_ma = calculate_sma(
        prices,
        5
    )

    long_ma = calculate_sma(
        prices,
        14
    )

    if (
        rsi is None
        or short_ma is None
        or long_ma is None
    ):

        return {
            "action": "HOLD",

            "description": (
                "Insufficient local "
                "history for full analysis."
            ),

            "confidence": 50.0,

            "trend": "NEUTRAL",

            "rsi": rsi,

            "price": price,

            "short_ma": short_ma,

            "long_ma": long_ma,

            "source": (
                "Upupway AI v1.3.1 "
                "local signal engine"
            ),

            "analysis_status": "WARMING_UP",

            "history_points": history_count,

            "paper_only": True,

            "generated_at": now_iso(),
        }

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = 50

    reasons = []

    if rsi < 30:

        score += 20

        reasons.append(
            "RSI indicates oversold conditions."
        )

    elif rsi < 40:

        score += 10

        reasons.append(
            "RSI is moderately low."
        )

    elif rsi > 70:

        score -= 20

        reasons.append(
            "RSI indicates overbought conditions."
        )

    elif rsi > 60:

        score -= 10

        reasons.append(
            "RSI is moderately high."
        )

    else:

        reasons.append(
            "RSI is in a neutral range."
        )

    if short_ma > long_ma:

        score += 20

        reasons.append(
            "Short-term moving average "
            "is above long-term moving average."
        )

    else:

        score -= 20

        reasons.append(
            "Short-term moving average "
            "is below long-term moving average."
        )

    if price > long_ma:

        score += 10

        reasons.append(
            "Price is above the long-term "
            "moving average."
        )

    else:

        score -= 10

        reasons.append(
            "Price is below the long-term "
            "moving average."
        )

    score = clamp(
        score,
        0,
        100
    )

    if score >= 65:
        action = "BUY"

    elif score <= 35:
        action = "SELL"

    else:
        action = "HOLD"

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

    return {
        "action": action,

        "description": " ".join(
            reasons
        ),

        "confidence": round(
            score,
            2
        ),

        "trend": trend,

        "rsi": rsi,

        "price": price,

        "short_ma": round(
            short_ma,
            2
        ),

        "long_ma": round(
            long_ma,
            2
        ),

        "source": (
            "Upupway AI v1.3.1 "
            "local signal engine"
        ),

        "analysis_status": "READY",

        "history_points": history_count,

        "paper_only": True,

        "generated_at": now_iso(),
    }


# ============================================================
# ACCOUNT VALUE
# ============================================================

def calculate_account_value(
    current_price=None
):

    if current_price is None:

        market = get_market_data()

        current_price = market[
            "price"
        ]

    value = (
        paper_account["cash"]
        + (
            paper_account["btc"]
            * current_price
        )
    )

    return round(
        value,
        2
    )


def update_account_metrics(
    current_price=None
):

    if current_price is None:

        market = get_market_data()

        current_price = market[
            "price"
        ]

    portfolio_value = (
        calculate_account_value(
            current_price
        )
    )

    btc_value = (
        paper_account["btc"]
        * current_price
    )

    entry_price = (
        paper_account[
            "entry_price"
        ]
    )

    if (
        entry_price is not None
        and paper_account["btc"] > 0
    ):

        unrealized = (
            current_price
            - entry_price
        ) * paper_account["btc"]

    else:

        unrealized = 0.0

    paper_account[
        "unrealized_profit_loss"
    ] = round(
        unrealized,
        2
    )

    paper_account[
        "profit_loss"
    ] = round(
        portfolio_value
        - paper_account[
            "starting_balance"
        ],
        2
    )

    paper_account[
        "portfolio_value"
    ] = portfolio_value

    roi = (
        (
            (
                portfolio_value
                / paper_account[
                    "starting_balance"
                ]
            ) - 1
        ) * 100
    )

    return {
        **paper_account,

        "btc_price": current_price,

        "btc_value": round(
            btc_value,
            2
        ),

        "roi_percent": round(
            roi,
            2
        ),
    }


# ============================================================
# TRADE RECORDING
# ============================================================

def add_trade(
    side,
    price,
    quantity,
    amount,
    reason,
    profit_loss=0.0,
    source="manual"
):

    trade = {
        "id": str(
            uuid.uuid4()
        ),

        "timestamp": now_iso(),

        "side": side,

        "symbol": "BTCUSDT",

        "price": round(
            price,
            2
        ),

        "quantity": round(
            quantity,
            8
        ),

        "amount": round(
            amount,
            2
        ),

        "reason": reason,

        "profit_loss": round(
            profit_loss,
            2
        ),

        "source": source,

        "paper_only": True,
    }

    trade_history.append(
        trade
    )

    return trade


# ============================================================
# PAPER BUY
# ============================================================

def execute_paper_buy(
    source="manual",
    reason="Manual paper buy"
):

    market = get_market_data()

    price = market[
        "price"
    ]

    if price <= 0:

        raise HTTPException(
            status_code=503,
            detail="Invalid BTC price."
        )

    if paper_account["btc"] > 0:

        raise HTTPException(
            status_code=400,
            detail=(
                "Paper account already "
                "holds BTC."
            ),
        )

    amount = (
        paper_account["cash"]
        * RISK_SETTINGS[
            "max_position_percent"
        ]
        / 100
    )

    if amount <= 0:

        raise HTTPException(
            status_code=400,
            detail="Insufficient paper cash."
        )

    quantity = (
        amount / price
    )

    paper_account[
        "cash"
    ] -= amount

    paper_account[
        "btc"
    ] += quantity

    paper_account[
        "entry_price"
    ] = price

    paper_account[
        "last_action"
    ] = "BUY"

    trade = add_trade(
        side="BUY",
        price=price,
        quantity=quantity,
        amount=amount,
        reason=reason,
        source=source,
    )

    return {
        "success": True,

        "message": (
            "Paper BUY executed."
        ),

        "trade": trade,

        "account": update_account_metrics(
            price
        ),

        "paper_only": True,
    }


# ============================================================
# PAPER SELL
# ============================================================

def execute_paper_sell(
    source="manual",
    reason="Manual paper sell"
):

    market = get_market_data()

    price = market[
        "price"
    ]

    if paper_account["btc"] <= 0:

        raise HTTPException(
            status_code=400,
            detail=(
                "No BTC position "
                "to sell."
            ),
        )

    quantity = paper_account[
        "btc"
    ]

    amount = (
        quantity * price
    )

    entry_price = (
        paper_account[
            "entry_price"
        ]
    )

    pnl = 0.0

    if entry_price is not None:

        pnl = (
            price
            - entry_price
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

    trade = add_trade(
        side="SELL",
        price=price,
        quantity=quantity,
        amount=amount,
        reason=reason,
        profit_loss=pnl,
        source=source,
    )

    if pnl > 0:
        auto_trading["wins"] += 1

    elif pnl < 0:
        auto_trading["losses"] += 1

    return {
        "success": True,

        "message": (
            "Paper SELL executed."
        ),

        "trade": trade,

        "account": update_account_metrics(
            price
        ),

        "paper_only": True,
    }


# ============================================================
# RISK
# ============================================================

def get_consecutive_losses():

    consecutive = 0

    for trade in reversed(
        trade_history
    ):

        if trade["side"] != "SELL":
            continue

        pnl = safe_float(
            trade.get(
                "profit_loss"
            ),
            0
        )

        if pnl < 0:

            consecutive += 1

        else:

            break

    return consecutive


def get_risk_snapshot(
    current_price=None
):

    account = update_account_metrics(
        current_price
    )

    starting = paper_account[
        "starting_balance"
    ]

    current = account[
        "portfolio_value"
    ]

    loss_percent = 0.0

    if current < starting:

        loss_percent = (
            (
                starting
                - current
            )
            / starting
        ) * 100

    consecutive_losses = (
        get_consecutive_losses()
    )

    return {
        "portfolio_value": current,

        "profit_loss": account[
            "profit_loss"
        ],

        "loss_percent": round(
            loss_percent,
            2
        ),

        "consecutive_losses":
            consecutive_losses,

        "daily_loss_limit_hit": (
            loss_percent
            >= RISK_SETTINGS[
                "daily_loss_limit_percent"
            ]
        ),

        "consecutive_loss_limit_hit": (
            consecutive_losses
            >= RISK_SETTINGS[
                "max_consecutive_losses"
            ]
        ),

        "risk_settings":
            RISK_SETTINGS,
    }


# ============================================================
# POSITION EXIT
# ============================================================

def check_position_exit(
    current_price
):

    if paper_account["btc"] <= 0:
        return None

    entry_price = (
        paper_account[
            "entry_price"
        ]
    )

    if entry_price is None:
        return None

    stop_price = (
        entry_price
        * (
            1
            - RISK_SETTINGS[
                "stop_loss_percent"
            ] / 100
        )
    )

    target_price = (
        entry_price
        * (
            1
            + RISK_SETTINGS[
                "take_profit_percent"
            ] / 100
        )
    )

    if current_price <= stop_price:

        return execute_paper_sell(
            source="risk_engine",
            reason="Stop-loss triggered"
        )

    if current_price >= target_price:

        return execute_paper_sell(
            source="risk_engine",
            reason="Take-profit triggered"
        )

    return None


# ============================================================
# API MODELS
# ============================================================

class AutoTradingToggle(BaseModel):

    enabled: bool


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():

    return {
        "name": APP_NAME,

        "status": "online",

        "mode": "paper",

        "version": VERSION,

        "build_id": BUILD_ID,

        "message": (
            "Upupway AI trading "
            "backend is running."
        ),

        "real_money_trading": False,

        "paper_trading": True,
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

        "build_id": BUILD_ID,

        "mode": "paper",

        "timestamp": now_iso(),
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

        "mode": "paper",

        "market_data": "CoinGecko",

        "signal_engine": (
            "RSI + Moving Average "
            "+ Local Price History"
        ),

        "auto_trading":
            auto_trading["enabled"],

        "paper_trading": True,

        "real_money_trading": False,

        "api_keys_required": False,

        "local_history_points": len(
            local_price_history
        ),

        "collector_interval_seconds":
            COLLECTOR_INTERVAL_SECONDS,

        "collector_running":
            collector_task is not None
            and not collector_task.done(),

        "timestamp": now_iso(),
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

    return generate_signal()


# ============================================================
# HISTORY STATUS
# ============================================================

@app.get("/api/history-status")
def history_status():

    points = len(
        local_price_history
    )

    return {
        "success": True,

        "history_points": points,

        "required_points":
            LOCAL_HISTORY_REQUIRED_POINTS,

        "ready": (
            points
            >= LOCAL_HISTORY_REQUIRED_POINTS
        ),

        "max_points":
            LOCAL_HISTORY_MAX_POINTS,

        "collector_interval_seconds":
            COLLECTOR_INTERVAL_SECONDS,

        "collector_running":
            collector_task is not None
            and not collector_task.done(),

        "source":
            "local_market_snapshots",

        "note": (
            "Live signal generation "
            "uses locally collected "
            "market snapshots instead "
            "of repeatedly requesting "
            "CoinGecko market history."
        ),

        "paper_only": True,
    }


# ============================================================
# PAPER ACCOUNT
# ============================================================

@app.get("/api/paper-account")
def paper_account_endpoint():

    return update_account_metrics()


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

    return {
        "success": True,

        "count": len(
            trade_history
        ),

        "trades": trade_history,

        "paper_only": True,
    }


# ============================================================
# RISK SETTINGS
# ============================================================

@app.get("/api/risk-settings")
def risk_settings():

    return {
        "success": True,

        "risk_settings":
            RISK_SETTINGS,

        "paper_only": True,
    }


# ============================================================
# RISK SNAPSHOT
# ============================================================

@app.get("/api/risk")
def risk():

    return {
        "success": True,

        "risk": get_risk_snapshot(),

        "paper_only": True,
    }


# ============================================================
# AUTO TRADING STATUS
# ============================================================

@app.get("/api/auto-trading")
def auto_trading_status():

    market = get_market_data()

    current_price = market[
        "price"
    ]

    risk = get_risk_snapshot(
        current_price
    )

    cooldown_active = False

    if auto_trading[
        "last_trade_time"
    ]:

        elapsed = (
            time.time()
            - auto_trading[
                "last_trade_time"
            ]
        )

        cooldown_active = (
            elapsed
            < RISK_SETTINGS[
                "trade_cooldown_seconds"
            ]
        )

    return {
        "enabled":
            auto_trading["enabled"],

        "last_signal":
            auto_trading["last_signal"],

        "last_action":
            auto_trading["last_action"],

        "last_price":
            auto_trading["last_price"],

        "last_trade_time": (
            datetime.fromtimestamp(
                auto_trading[
                    "last_trade_time"
                ],
                timezone.utc
            ).isoformat()
            if auto_trading[
                "last_trade_time"
            ]
            else None
        ),

        "trades":
            auto_trading["trades"],

        "wins":
            auto_trading["wins"],

        "losses":
            auto_trading["losses"],

        "cooldown_active":
            cooldown_active,

        "risk":
            risk,

        "paper_only": True,
    }


# ============================================================
# TOGGLE AUTO TRADING
# ============================================================

@app.post("/api/auto-trading/toggle")
def toggle_auto_trading(
    payload: AutoTradingToggle
):

    auto_trading[
        "enabled"
    ] = payload.enabled

    return {
        "success": True,

        "enabled":
            auto_trading["enabled"],

        "message": (
            "Paper auto-trading enabled."
            if payload.enabled
            else
            "Paper auto-trading disabled."
        ),

        "paper_only": True,

        "real_money_trading": False,
    }


# ============================================================
# RUN AUTO TRADING
# ============================================================

@app.post("/api/auto-trading/run")
def run_auto_trading():

    if not auto_trading[
        "enabled"
    ]:

        return {
            "success": False,

            "action": "DISABLED",

            "message":
                "Auto-trading is disabled.",

            "paper_only": True,
        }

    market = get_market_data()

    current_price = market[
        "price"
    ]

    auto_trading[
        "last_price"
    ] = current_price

    # Risk exits first
    exit_result = check_position_exit(
        current_price
    )

    if exit_result:

        auto_trading[
            "last_action"
        ] = "SELL"

        auto_trading[
            "last_trade_time"
        ] = time.time()

        auto_trading[
            "trades"
        ] += 1

        return {
            "success": True,

            "action": "SELL",

            "reason": "Risk exit",

            "result": exit_result,

            "paper_only": True,
        }

    # Risk protection
    risk = get_risk_snapshot(
        current_price
    )

    if risk[
        "daily_loss_limit_hit"
    ]:

        return {
            "success": False,

            "action": "RISK_BLOCK",

            "message":
                "Daily loss limit reached.",

            "risk": risk,

            "paper_only": True,
        }

    if risk[
        "consecutive_loss_limit_hit"
    ]:

        return {
            "success": False,

            "action": "RISK_BLOCK",

            "message": (
                "Maximum consecutive "
                "losses reached."
            ),

            "risk": risk,

            "paper_only": True,
        }

    # Cooldown
    if auto_trading[
        "last_trade_time"
    ]:

        elapsed = (
            time.time()
            - auto_trading[
                "last_trade_time"
            ]
        )

        cooldown = RISK_SETTINGS[
            "trade_cooldown_seconds"
        ]

        if elapsed < cooldown:

            return {
                "success": False,

                "action": "COOLDOWN",

                "message":
                    "Trading cooldown is active.",

                "seconds_remaining":
                    round(
                        cooldown - elapsed,
                        2
                    ),

                "paper_only": True,
            }

    # Signal
    signal_data = generate_signal()

    action = signal_data[
        "action"
    ]

    confidence = signal_data[
        "confidence"
    ]

    auto_trading[
        "last_signal"
    ] = action

    # Confidence filter
    if (
        confidence
        < RISK_SETTINGS[
            "minimum_confidence"
        ]
    ):

        auto_trading[
            "last_action"
        ] = "HOLD"

        return {
            "success": True,

            "action": "HOLD",

            "message": (
                "Signal confidence "
                "is below the configured "
                "threshold."
            ),

            "signal": signal_data,

            "paper_only": True,
        }

    # BUY
    if action == "BUY":

        if paper_account["btc"] > 0:

            auto_trading[
                "last_action"
            ] = "HOLD"

            return {
                "success": True,

                "action": "HOLD",

                "message": (
                    "BTC position "
                    "already exists."
                ),

                "signal": signal_data,

                "paper_only": True,
            }

        result = execute_paper_buy(
            source="auto",
            reason=(
                "AI BUY signal "
                f"confidence={confidence}%"
            ),
        )

        auto_trading[
            "last_action"
        ] = "BUY"

        auto_trading[
            "last_trade_time"
        ] = time.time()

        auto_trading[
            "trades"
        ] += 1

        return {
            "success": True,

            "action": "BUY",

            "signal": signal_data,

            "result": result,

            "paper_only": True,
        }

    # SELL
    if action == "SELL":

        if paper_account["btc"] <= 0:

            auto_trading[
                "last_action"
            ] = "HOLD"

            return {
                "success": True,

                "action": "HOLD",

                "message": (
                    "No BTC position "
                    "available to sell."
                ),

                "signal": signal_data,

                "paper_only": True,
            }

        result = execute_paper_sell(
            source="auto",
            reason=(
                "AI SELL signal "
                f"confidence={confidence}%"
            ),
        )

        auto_trading[
            "last_action"
        ] = "SELL"

        auto_trading[
            "last_trade_time"
        ] = time.time()

        auto_trading[
            "trades"
        ] += 1

        return {
            "success": True,

            "action": "SELL",

            "signal": signal_data,

            "result": result,

            "paper_only": True,
        }

    # HOLD
    auto_trading[
        "last_action"
    ] = "HOLD"

    return {
        "success": True,

        "action": "HOLD",

        "signal": signal_data,

        "message":
            "No trade executed.",

        "paper_only": True,
    }


# ============================================================
# RESET
# ============================================================

@app.post("/api/paper-account/reset")
def reset_paper_account():

    global paper_account
    global trade_history

    paper_account = {
        "starting_balance": 10000.0,
        "cash": 10000.0,
        "btc": 0.0,
        "entry_price": None,
        "last_action": "NONE",
        "profit_loss": 0.0,
        "realized_profit_loss": 0.0,
        "unrealized_profit_loss": 0.0,
        "portfolio_value": 10000.0,
    }

    trade_history = []

    auto_trading[
        "last_signal"
    ] = "NONE"

    auto_trading[
        "last_action"
    ] = "NONE"

    auto_trading[
        "last_price"
    ] = None

    auto_trading[
        "last_trade_time"
    ] = None

    auto_trading[
        "trades"
    ] = 0

    auto_trading[
        "wins"
    ] = 0

    auto_trading[
        "losses"
    ] = 0

    return {
        "success": True,

        "message":
            "Paper account has been reset.",

        "account":
            paper_account,

        "paper_only": True,
    }


# ============================================================
# BACKTEST
# ============================================================

@app.post("/api/backtest")
def backtest(
    days: int = 30
):

    prices = get_historical_prices(
        days
    )

    if len(prices) < 30:

        raise HTTPException(
            status_code=503,
            detail=(
                "Not enough historical "
                "data for backtest."
            ),
        )

    starting_cash = 10000.0

    cash = starting_cash

    btc = 0.0

    entry_price = None

    trades = 0
    wins = 0
    losses = 0

    for index in range(
        20,
        len(prices)
    ):

        current_price = prices[
            index
        ]

        window = prices[
            :index + 1
        ]

        rsi = calculate_rsi(
            window,
            14
        )

        short_ma = calculate_sma(
            window,
            5
        )

        long_ma = calculate_sma(
            window,
            14
        )

        if (
            rsi is None
            or short_ma is None
            or long_ma is None
        ):
            continue

        score = 50

        if rsi < 30:
            score += 20

        elif rsi < 40:
            score += 10

        elif rsi > 70:
            score -= 20

        elif rsi > 60:
            score -= 10

        if short_ma > long_ma:
            score += 20

        else:
            score -= 20

        if current_price > long_ma:
            score += 10

        else:
            score -= 10

        score = clamp(
            score,
            0,
            100
        )

        action = (
            "BUY"
            if score >= 65
            else
            "SELL"
            if score <= 35
            else
            "HOLD"
        )

        if (
            action == "BUY"
            and btc == 0
        ):

            amount = (
                cash * 0.10
            )

            if amount > 0:

                btc = (
                    amount
                    / current_price
                )

                cash -= amount

                entry_price = (
                    current_price
                )

                trades += 1

        elif (
            action == "SELL"
            and btc > 0
        ):

            proceeds = (
                btc
                * current_price
            )

            pnl = 0.0

            if entry_price is not None:

                pnl = (
                    current_price
                    - entry_price
                ) * btc

            cash += proceeds

            btc = 0.0

            entry_price = None

            trades += 1

            if pnl > 0:
                wins += 1

            elif pnl < 0:
                losses += 1

    final_price = prices[-1]

    final_value = (
        cash
        + btc * final_price
    )

    buy_hold_btc = (
        starting_cash
        / prices[0]
    )

    buy_hold_value = (
        buy_hold_btc
        * final_price
    )

    strategy_return = (
        (
            final_value
            / starting_cash
        ) - 1
    ) * 100

    buy_hold_return = (
        (
            buy_hold_value
            / starting_cash
        ) - 1
    ) * 100

    win_rate = (
        wins
        / (wins + losses)
        * 100
        if wins + losses > 0
        else 0.0
    )

    return {
        "success": True,

        "strategy":
            "RSI + Moving Average",

        "days": days,

        "data_points":
            len(prices),

        "starting_balance":
            starting_cash,

        "final_value":
            round(
                final_value,
                2
            ),

        "strategy_return_percent":
            round(
                strategy_return,
                2
            ),

        "buy_and_hold_return_percent":
            round(
                buy_hold_return,
                2
            ),

        "trades": trades,

        "wins": wins,

        "losses": losses,

        "win_rate_percent":
            round(
                win_rate,
                2
            ),

        "paper_only": True,

        "note": (
            "Historical simulation only. "
            "Past performance does not "
            "guarantee future results."
        ),
    }


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
)
