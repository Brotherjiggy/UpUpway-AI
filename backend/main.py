from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from collections import deque
from datetime import datetime, timezone
import requests
import time
import statistics
import uuid


# ============================================================
# UPUPWAY AI
# Backend v1.3.0
# Paper Trading Only
# ============================================================

APP_NAME = "UpUpway AI"
VERSION = "1.3.0"
BUILD_ID = "2026-09-05-v1.3.0"

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"

REQUEST_TIMEOUT = 15

# ------------------------------------------------------------
# Market-data protection
# ------------------------------------------------------------

MARKET_CACHE_TTL = 300.0
PROVIDER_MIN_INTERVAL = 120.0
STALE_MARKET_MAX_AGE = 86400.0

HISTORY_CACHE_TTL = 900.0
HISTORY_PROVIDER_MIN_INTERVAL = 600.0

market_cache: Optional[Dict[str, Any]] = None
market_cache_time = 0.0
last_market_provider_request = 0.0

historical_cache: Dict[int, Dict[str, Any]] = {}
last_history_provider_request: Dict[int, float] = {}

# Local rolling BTC price history.
# This prevents the live signal engine from repeatedly requesting
# CoinGecko's market_chart endpoint.
local_price_history = deque(maxlen=500)


# ============================================================
# FastAPI
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description="UpUpway AI paper-trading backend."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Paper account
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
# Auto trading
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
# Helpers
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def safe_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def record_local_price(price: float):
    """
    Store the latest BTC price locally.

    Format:
    {
        timestamp: unix timestamp,
        price: BTC price
    }
    """

    if price is None or price <= 0:
        return

    current_time = time.time()

    if local_price_history:
        last = local_price_history[-1]

        # Avoid duplicate snapshots at almost the same moment.
        if current_time - last["timestamp"] < 1:
            last["price"] = price
            return

    local_price_history.append({
        "timestamp": current_time,
        "price": price
    })


# ============================================================
# CoinGecko market data
# ============================================================

def fetch_market_from_provider() -> Dict[str, Any]:
    global last_market_provider_request

    url = f"{COINGECKO_BASE_URL}/simple/price"

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

    bitcoin = data.get("bitcoin", {})
    ethereum = data.get("ethereum", {})
    solana = data.get("solana", {})

    btc_price = safe_float(bitcoin.get("usd"))

    if btc_price is None:
        raise RuntimeError("Bitcoin price was not returned by CoinGecko.")

    market = {
        "symbol": "BTCUSDT",
        "price": btc_price,
        "change_24h": safe_float(bitcoin.get("usd_24h_change"), 0.0),
        "volume_24h": None,

        "eth_price": safe_float(ethereum.get("usd")),
        "eth_change_24h": safe_float(
            ethereum.get("usd_24h_change"),
            0.0
        ),

        "sol_price": safe_float(solana.get("usd")),
        "sol_change_24h": safe_float(
            solana.get("usd_24h_change"),
            0.0
        ),

        "source": "CoinGecko",
        "updated_at": now_iso(),
    }

    last_market_provider_request = time.time()

    return market


def get_market_data() -> Dict[str, Any]:
    """
    Get market data using:
    1. Fresh cache
    2. Provider refresh
    3. Stale cache fallback
    """

    global market_cache
    global market_cache_time

    current_time = time.time()

    # --------------------------------------------------------
    # Fresh cache
    # --------------------------------------------------------

    if market_cache is not None:
        cache_age = current_time - market_cache_time

        if cache_age < MARKET_CACHE_TTL:
            market = dict(market_cache)
            market["cache"] = "fresh"
            market["cache_age_seconds"] = round(cache_age, 2)

            record_local_price(market["price"])

            return market

    # --------------------------------------------------------
    # Provider cooldown
    # --------------------------------------------------------

    provider_age = current_time - last_market_provider_request

    if (
        market_cache is not None
        and provider_age < PROVIDER_MIN_INTERVAL
    ):
        market = dict(market_cache)

        market["cache"] = "cooldown"
        market["cache_age_seconds"] = round(
            current_time - market_cache_time,
            2
        )

        record_local_price(market["price"])

        return market

    # --------------------------------------------------------
    # Provider request
    # --------------------------------------------------------

    try:
        market = fetch_market_from_provider()

        market_cache = dict(market)
        market_cache_time = current_time

        market["cache"] = "provider"

        record_local_price(market["price"])

        return market

    except Exception as exc:

        # ----------------------------------------------------
        # Stale cache fallback
        # ----------------------------------------------------

        if market_cache is not None:
            stale_age = current_time - market_cache_time

            if stale_age <= STALE_MARKET_MAX_AGE:

                market = dict(market_cache)

                market["cache"] = "stale_fallback"
                market["cache_age_seconds"] = round(
                    stale_age,
                    2
                )
                market["warning"] = (
                    "CoinGecko temporarily unavailable. "
                    "Using cached market data."
                )

                record_local_price(market["price"])

                return market

        raise HTTPException(
            status_code=503,
            detail=f"Market data provider unavailable: {exc}"
        )


# ============================================================
# Historical data
# ============================================================

def get_historical_prices(days: int = 30) -> List[float]:
    """
    Historical data is used mainly for backtesting.

    Live signal generation does NOT use this function.
    """

    global historical_cache
    global last_history_provider_request

    days = max(1, min(days, 365))

    current_time = time.time()

    cached = historical_cache.get(days)

    if cached:
        age = current_time - cached["timestamp"]

        if age < HISTORY_CACHE_TTL:
            return cached["prices"]

    last_request = last_history_provider_request.get(days, 0)

    if current_time - last_request < HISTORY_PROVIDER_MIN_INTERVAL:

        if cached:
            return cached["prices"]

        raise HTTPException(
            status_code=503,
            detail="Historical data provider is temporarily on cooldown."
        )

    url = f"{COINGECKO_BASE_URL}/coins/bitcoin/market_chart"

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

        prices_raw = data.get("prices", [])

        prices = []

        for item in prices_raw:
            if len(item) >= 2:
                price = safe_float(item[1])

                if price is not None and price > 0:
                    prices.append(price)

        if len(prices) < 20:
            raise RuntimeError(
                "Not enough historical Bitcoin prices returned."
            )

        historical_cache[days] = {
            "timestamp": current_time,
            "prices": prices,
        }

        last_history_provider_request[days] = current_time

        return prices

    except Exception as exc:

        if cached:
            return cached["prices"]

        raise HTTPException(
            status_code=503,
            detail=f"Historical data unavailable: {exc}"
        )


# ============================================================
# Technical indicators
# ============================================================

def calculate_sma(
    prices: List[float],
    period: int
) -> Optional[float]:

    if len(prices) < period:
        return None

    values = prices[-period:]

    return statistics.mean(values)


def calculate_rsi(
    prices: List[float],
    period: int = 14
) -> Optional[float]:

    if len(prices) <= period:
        return None

    changes = []

    for i in range(1, len(prices)):
        changes.append(
            prices[i] - prices[i - 1]
        )

    recent_changes = changes[-period:]

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

    relative_strength = (
        average_gain / average_loss
    )

    rsi = 100 - (
        100 / (1 + relative_strength)
    )

    return round(clamp(rsi, 0, 100), 2)


# ============================================================
# Signal engine
# ============================================================

def generate_signal() -> Dict[str, Any]:

    market = get_market_data()

    price = market["price"]

    # The important change in v1.3.0:
    # use locally collected prices rather than repeatedly
    # requesting CoinGecko market_chart.

    local_prices = [
        item["price"]
        for item in local_price_history
    ]

    history_count = len(local_prices)

    # --------------------------------------------------------
    # Warming-up state
    # --------------------------------------------------------

    if history_count < 20:

        change_24h = safe_float(
            market.get("change_24h"),
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
                "UpUpway AI is collecting local BTC price "
                "history before generating a full technical signal."
            ),
            "confidence": 50.0,
            "trend": trend,
            "rsi": None,
            "price": price,
            "short_ma": None,
            "long_ma": None,
            "source": "UpUpway AI v1.3.0 local signal engine",
            "analysis_status": "WARMING_UP",
            "history_points": history_count,
            "required_history_points": 20,
            "paper_only": True,
            "generated_at": now_iso(),
        }

    # --------------------------------------------------------
    # Indicators
    # --------------------------------------------------------

    rsi = calculate_rsi(
        local_prices,
        14
    )

    short_ma = calculate_sma(
        local_prices,
        5
    )

    long_ma = calculate_sma(
        local_prices,
        14
    )

    if (
        rsi is None
        or short_ma is None
        or long_ma is None
    ):

        return {
            "action": "HOLD",
            "description": "Insufficient local history for full analysis.",
            "confidence": 50.0,
            "trend": "NEUTRAL",
            "rsi": rsi,
            "price": price,
            "short_ma": short_ma,
            "long_ma": long_ma,
            "source": "UpUpway AI v1.3.0 local signal engine",
            "analysis_status": "WARMING_UP",
            "history_points": history_count,
            "paper_only": True,
            "generated_at": now_iso(),
        }

    # --------------------------------------------------------
    # Score
    # --------------------------------------------------------

    score = 50

    reasons = []

    # RSI
    if rsi < 30:
        score += 20
        reasons.append("RSI indicates oversold conditions.")

    elif rsi < 40:
        score += 10
        reasons.append("RSI is moderately low.")

    elif rsi > 70:
        score -= 20
        reasons.append("RSI indicates overbought conditions.")

    elif rsi > 60:
        score -= 10
        reasons.append("RSI is moderately high.")

    else:
        reasons.append("RSI is in a neutral range.")

    # Moving averages
    if short_ma > long_ma:
        score += 20
        reasons.append(
            "Short-term moving average is above "
            "long-term moving average."
        )

    else:
        score -= 20
        reasons.append(
            "Short-term moving average is below "
            "long-term moving average."
        )

    # Price vs long MA
    if price > long_ma:
        score += 10
        reasons.append(
            "Price is above the long-term moving average."
        )

    else:
        score -= 10
        reasons.append(
            "Price is below the long-term moving average."
        )

    score = clamp(score, 0, 100)

    # --------------------------------------------------------
    # Action
    # --------------------------------------------------------

    if score >= 65:
        action = "BUY"

    elif score <= 35:
        action = "SELL"

    else:
        action = "HOLD"

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

    return {
        "action": action,
        "description": " ".join(reasons),
        "confidence": round(score, 2),
        "trend": trend,
        "rsi": rsi,
        "price": price,
        "short_ma": round(short_ma, 2),
        "long_ma": round(long_ma, 2),
        "source": "UpUpway AI v1.3.0 local signal engine",
        "analysis_status": "READY",
        "history_points": history_count,
        "paper_only": True,
        "generated_at": now_iso(),
    }


# ============================================================
# Paper portfolio calculations
# ============================================================

def calculate_account_value(
    current_price: Optional[float] = None
) -> float:

    if current_price is None:

        market = get_market_data()

        current_price = market["price"]

    portfolio_value = (
        paper_account["cash"]
        + (
            paper_account["btc"]
            * current_price
        )
    )

    return round(portfolio_value, 2)


def update_account_metrics(
    current_price: Optional[float] = None
):

    if current_price is None:

        market = get_market_data()

        current_price = market["price"]

    portfolio_value = calculate_account_value(
        current_price
    )

    btc_value = (
        paper_account["btc"]
        * current_price
    )

    entry_price = paper_account["entry_price"]

    if (
        entry_price is not None
        and paper_account["btc"] > 0
    ):

        unrealized = (
            current_price - entry_price
        ) * paper_account["btc"]

    else:
        unrealized = 0.0

    paper_account["unrealized_profit_loss"] = round(
        unrealized,
        2
    )

    paper_account["profit_loss"] = round(
        portfolio_value
        - paper_account["starting_balance"],
        2
    )

    paper_account["portfolio_value"] = portfolio_value

    return {
        **paper_account,
        "btc_price": current_price,
        "btc_value": round(btc_value, 2),
        "roi_percent": round(
            (
                (
                    portfolio_value
                    / paper_account["starting_balance"]
                ) - 1
            ) * 100,
            2
        ),
    }


# ============================================================
# Trade recording
# ============================================================

def add_trade(
    side: str,
    price: float,
    quantity: float,
    amount: float,
    reason: str,
    profit_loss: float = 0.0,
    source: str = "manual"
):

    trade = {
        "id": str(uuid.uuid4()),
        "timestamp": now_iso(),
        "side": side,
        "symbol": "BTCUSDT",
        "price": round(price, 2),
        "quantity": round(quantity, 8),
        "amount": round(amount, 2),
        "reason": reason,
        "profit_loss": round(profit_loss, 2),
        "source": source,
        "paper_only": True,
    }

    trade_history.append(trade)

    return trade


# ============================================================
# Paper BUY
# ============================================================

def execute_paper_buy(
    source: str = "manual",
    reason: str = "Manual paper buy"
):

    market = get_market_data()

    price = market["price"]

    if price <= 0:
        raise HTTPException(
            status_code=503,
            detail="Invalid BTC price."
        )

    if paper_account["btc"] > 0:
        raise HTTPException(
            status_code=400,
            detail="Paper account already holds BTC."
        )

    max_position_value = (
        paper_account["cash"]
        * RISK_SETTINGS["max_position_percent"]
        / 100
    )

    if max_position_value <= 0:
        raise HTTPException(
            status_code=400,
            detail="Insufficient paper cash."
        )

    quantity = (
        max_position_value
        / price
    )

    amount = quantity * price

    paper_account["cash"] -= amount
    paper_account["btc"] += quantity
    paper_account["entry_price"] = price
    paper_account["last_action"] = "BUY"

    trade = add_trade(
        side="BUY",
        price=price,
        quantity=quantity,
        amount=amount,
        reason=reason,
        source=source,
    )

    update_account_metrics(price)

    return {
        "success": True,
        "message": "Paper BUY executed.",
        "trade": trade,
        "account": update_account_metrics(price),
        "paper_only": True,
    }


# ============================================================
# Paper SELL
# ============================================================

def execute_paper_sell(
    source: str = "manual",
    reason: str = "Manual paper sell"
):

    market = get_market_data()

    price = market["price"]

    if paper_account["btc"] <= 0:
        raise HTTPException(
            status_code=400,
            detail="No BTC position to sell."
        )

    quantity = paper_account["btc"]

    amount = quantity * price

    entry_price = paper_account["entry_price"]

    profit_loss = 0.0

    if entry_price is not None:
        profit_loss = (
            price - entry_price
        ) * quantity

    paper_account["cash"] += amount
    paper_account["btc"] = 0.0
    paper_account["entry_price"] = None
    paper_account["last_action"] = "SELL"

    paper_account["realized_profit_loss"] += profit_loss

    trade = add_trade(
        side="SELL",
        price=price,
        quantity=quantity,
        amount=amount,
        reason=reason,
        profit_loss=profit_loss,
        source=source,
    )

    # Track wins/losses
    if profit_loss > 0:
        auto_trading["wins"] += 1

    elif profit_loss < 0:
        auto_trading["losses"] += 1

    update_account_metrics(price)

    return {
        "success": True,
        "message": "Paper SELL executed.",
        "trade": trade,
        "account": update_account_metrics(price),
        "paper_only": True,
    }


# ============================================================
# Risk engine
# ============================================================

def get_consecutive_losses() -> int:

    consecutive = 0

    for trade in reversed(trade_history):

        if trade["side"] != "SELL":
            continue

        pnl = safe_float(
            trade.get("profit_loss"),
            0
        )

        if pnl < 0:
            consecutive += 1

        else:
            break

    return consecutive


def get_risk_snapshot(
    current_price: Optional[float] = None
):

    account = update_account_metrics(
        current_price
    )

    starting_balance = paper_account[
        "starting_balance"
    ]

    current_value = account[
        "portfolio_value"
    ]

    loss_percent = 0.0

    if current_value < starting_balance:

        loss_percent = (
            (
                starting_balance
                - current_value
            )
            / starting_balance
        ) * 100

    consecutive_losses = get_consecutive_losses()

    daily_loss_limit_hit = (
        loss_percent
        >= RISK_SETTINGS["daily_loss_limit_percent"]
    )

    consecutive_loss_limit_hit = (
        consecutive_losses
        >= RISK_SETTINGS["max_consecutive_losses"]
    )

    return {
        "portfolio_value": current_value,
        "profit_loss": account["profit_loss"],
        "loss_percent": round(loss_percent, 2),
        "consecutive_losses": consecutive_losses,
        "daily_loss_limit_hit": daily_loss_limit_hit,
        "consecutive_loss_limit_hit": consecutive_loss_limit_hit,
        "risk_settings": RISK_SETTINGS,
    }


# ============================================================
# Auto-trading exit management
# ============================================================

def check_position_exit(
    current_price: float
):

    if paper_account["btc"] <= 0:
        return None

    entry_price = paper_account[
        "entry_price"
    ]

    if entry_price is None:
        return None

    stop_loss_price = (
        entry_price
        * (
            1
            - RISK_SETTINGS["stop_loss_percent"]
            / 100
        )
    )

    take_profit_price = (
        entry_price
        * (
            1
            + RISK_SETTINGS["take_profit_percent"]
            / 100
        )
    )

    if current_price <= stop_loss_price:

        return execute_paper_sell(
            source="risk_engine",
            reason="Stop-loss triggered"
        )

    if current_price >= take_profit_price:

        return execute_paper_sell(
            source="risk_engine",
            reason="Take-profit triggered"
        )

    return None


# ============================================================
# API Models
# ============================================================

class AutoTradingToggle(BaseModel):
    enabled: bool


# ============================================================
# Root
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
            "Upupway AI trading backend is running."
        ),
        "real_money_trading": False,
        "paper_trading": True,
    }


# ============================================================
# Health
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
# Status
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
        "signal_engine": "RSI + Moving Average + Local Price History",
        "auto_trading": auto_trading["enabled"],
        "paper_trading": True,
        "real_money_trading": False,
        "api_keys_required": False,
        "local_history_points": len(
            local_price_history
        ),
        "timestamp": now_iso(),
    }


# ============================================================
# Market
# ============================================================

@app.get("/api/market")
def market():

    return get_market_data()


# ============================================================
# Signal
# ============================================================

@app.get("/api/signal")
def signal():

    return generate_signal()


# ============================================================
# Local history status
# ============================================================

@app.get("/api/history-status")
def history_status():

    points = len(local_price_history)

    return {
        "success": True,
        "history_points": points,
        "required_points": 20,
        "ready": points >= 20,
        "max_points": 500,
        "source": "local_market_snapshots",
        "note": (
            "Live signal generation uses local snapshots "
            "instead of repeatedly requesting CoinGecko "
            "historical market data."
        ),
        "paper_only": True,
    }


# ============================================================
# Paper account
# ============================================================

@app.get("/api/paper-account")
def paper_account_endpoint():

    return update_account_metrics()


# ============================================================
# Paper BUY
# ============================================================

@app.post("/api/paper-buy")
def paper_buy():

    return execute_paper_buy()


# ============================================================
# Paper SELL
# ============================================================

@app.post("/api/paper-sell")
def paper_sell():

    return execute_paper_sell()


# ============================================================
# Trade history
# ============================================================

@app.get("/api/trades")
def trades():

    return {
        "success": True,
        "count": len(trade_history),
        "trades": trade_history,
        "paper_only": True,
    }


# ============================================================
# Risk settings
# ============================================================

@app.get("/api/risk-settings")
def risk_settings():

    return {
        "success": True,
        "risk_settings": RISK_SETTINGS,
        "paper_only": True,
    }


# ============================================================
# Risk snapshot
# ============================================================

@app.get("/api/risk")
def risk():

    return {
        "success": True,
        "risk": get_risk_snapshot(),
        "paper_only": True,
    }


# ============================================================
# Auto trading status
# ============================================================

@app.get("/api/auto-trading")
def auto_trading_status():

    market = get_market_data()

    current_price = market["price"]

    risk = get_risk_snapshot(
        current_price
    )

    cooldown_active = False

    if auto_trading["last_trade_time"]:

        elapsed = (
            time.time()
            - auto_trading["last_trade_time"]
        )

        cooldown_active = (
            elapsed
            < RISK_SETTINGS[
                "trade_cooldown_seconds"
            ]
        )

    return {
        "enabled": auto_trading["enabled"],
        "last_signal": auto_trading["last_signal"],
        "last_action": auto_trading["last_action"],
        "last_price": auto_trading["last_price"],
        "last_trade_time": (
            datetime.fromtimestamp(
                auto_trading["last_trade_time"],
                timezone.utc
            ).isoformat()
            if auto_trading["last_trade_time"]
            else None
        ),
        "trades": auto_trading["trades"],
        "wins": auto_trading["wins"],
        "losses": auto_trading["losses"],
        "cooldown_active": cooldown_active,
        "risk": risk,
        "paper_only": True,
    }


# ============================================================
# Toggle auto trading
# ============================================================

@app.post("/api/auto-trading/toggle")
def toggle_auto_trading(
    payload: AutoTradingToggle
):

    auto_trading["enabled"] = payload.enabled

    return {
        "success": True,
        "enabled": auto_trading["enabled"],
        "message": (
            "Paper auto-trading enabled."
            if payload.enabled
            else "Paper auto-trading disabled."
        ),
        "paper_only": True,
        "real_money_trading": False,
    }


# ============================================================
# Run auto trading
# ============================================================

@app.post("/api/auto-trading/run")
def run_auto_trading():

    if not auto_trading["enabled"]:

        return {
            "success": False,
            "action": "DISABLED",
            "message": "Auto-trading is disabled.",
            "paper_only": True,
        }

    market = get_market_data()

    current_price = market["price"]

    auto_trading["last_price"] = current_price

    # --------------------------------------------------------
    # First manage an existing position.
    # --------------------------------------------------------

    exit_result = check_position_exit(
        current_price
    )

    if exit_result:

        auto_trading["last_action"] = "SELL"
        auto_trading["last_trade_time"] = time.time()
        auto_trading["trades"] += 1

        return {
            "success": True,
            "action": "SELL",
            "reason": "Risk exit",
            "result": exit_result,
            "paper_only": True,
        }

    # --------------------------------------------------------
    # Risk protection
    # --------------------------------------------------------

    risk = get_risk_snapshot(
        current_price
    )

    if risk["daily_loss_limit_hit"]:

        return {
            "success": False,
            "action": "RISK_BLOCK",
            "message": "Daily loss limit reached.",
            "risk": risk,
            "paper_only": True,
        }

    if risk["consecutive_loss_limit_hit"]:

        return {
            "success": False,
            "action": "RISK_BLOCK",
            "message": (
                "Maximum consecutive losses reached."
            ),
            "risk": risk,
            "paper_only": True,
        }

    # --------------------------------------------------------
    # Cooldown
    # --------------------------------------------------------

    if auto_trading["last_trade_time"]:

        elapsed = (
            time.time()
            - auto_trading["last_trade_time"]
        )

        if (
            elapsed
            < RISK_SETTINGS[
                "trade_cooldown_seconds"
            ]
        ):

            return {
                "success": False,
                "action": "COOLDOWN",
                "message": (
                    "Trading cooldown is active."
                ),
                "seconds_remaining": round(
                    RISK_SETTINGS[
                        "trade_cooldown_seconds"
                    ]
                    - elapsed,
                    2
                ),
                "paper_only": True,
            }

    # --------------------------------------------------------
    # Generate signal
    # --------------------------------------------------------

    signal_data = generate_signal()

    action = signal_data["action"]
    confidence = signal_data["confidence"]

    auto_trading["last_signal"] = action

    # --------------------------------------------------------
    # Confidence filter
    # --------------------------------------------------------

    if (
        confidence
        < RISK_SETTINGS["minimum_confidence"]
    ):

        auto_trading["last_action"] = "HOLD"

        return {
            "success": True,
            "action": "HOLD",
            "message": (
                "Signal confidence is below "
                "the configured threshold."
            ),
            "signal": signal_data,
            "paper_only": True,
        }

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    if action == "BUY":

        if paper_account["btc"] > 0:

            auto_trading["last_action"] = "HOLD"

            return {
                "success": True,
                "action": "HOLD",
                "message": (
                    "BTC position already exists."
                ),
                "signal": signal_data,
                "paper_only": True,
            }

        result = execute_paper_buy(
            source="auto",
            reason=(
                "AI BUY signal "
                f"confidence={confidence}%"
            )
        )

        auto_trading["last_action"] = "BUY"
        auto_trading["last_trade_time"] = time.time()
        auto_trading["trades"] += 1

        return {
            "success": True,
            "action": "BUY",
            "signal": signal_data,
            "result": result,
            "paper_only": True,
        }

    # --------------------------------------------------------
    # SELL
    # --------------------------------------------------------

    if action == "SELL":

        if paper_account["btc"] <= 0:

            auto_trading["last_action"] = "HOLD"

            return {
                "success": True,
                "action": "HOLD",
                "message": (
                    "No BTC position available to sell."
                ),
                "signal": signal_data,
                "paper_only": True,
            }

        result = execute_paper_sell(
            source="auto",
            reason=(
                "AI SELL signal "
                f"confidence={confidence}%"
            )
        )

        auto_trading["last_action"] = "SELL"
        auto_trading["last_trade_time"] = time.time()
        auto_trading["trades"] += 1

        return {
            "success": True,
            "action": "SELL",
            "signal": signal_data,
            "result": result,
            "paper_only": True,
        }

    # --------------------------------------------------------
    # HOLD
    # --------------------------------------------------------

    auto_trading["last_action"] = "HOLD"

    return {
        "success": True,
        "action": "HOLD",
        "signal": signal_data,
        "message": "No trade executed.",
        "paper_only": True,
    }


# ============================================================
# Reset paper account
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

    auto_trading["last_signal"] = "NONE"
    auto_trading["last_action"] = "NONE"
    auto_trading["last_price"] = None
    auto_trading["last_trade_time"] = None
    auto_trading["trades"] = 0
    auto_trading["wins"] = 0
    auto_trading["losses"] = 0

    return {
        "success": True,
        "message": "Paper account has been reset.",
        "account": paper_account,
        "paper_only": True,
    }


# ============================================================
# Backtest
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
            detail="Not enough historical data for backtest."
        )

    starting_cash = 10000.0
    cash = starting_cash
    btc = 0.0

    entry_price = None

    trades = 0
    wins = 0
    losses = 0

    equity_curve = []

    for index in range(20, len(prices)):

        current_price = prices[index]

        window = prices[:index + 1]

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

        signal_action = (
            "BUY"
            if score >= 65
            else
            "SELL"
            if score <= 35
            else
            "HOLD"
        )

        # BUY
        if (
            signal_action == "BUY"
            and btc == 0
        ):

            amount = cash * 0.10

            if amount > 0:

                btc = amount / current_price
                cash -= amount
                entry_price = current_price

                trades += 1

        # SELL
        elif (
            signal_action == "SELL"
            and btc > 0
        ):

            proceeds = btc * current_price

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

        equity = (
            cash
            + btc * current_price
        )

        equity_curve.append(equity)

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
        wins / (wins + losses) * 100
        if wins + losses > 0
        else 0.0
    )

    return {
        "success": True,
        "strategy": "RSI + Moving Average",
        "days": days,
        "data_points": len(prices),
        "starting_balance": starting_cash,
        "final_value": round(
            final_value,
            2
        ),
        "strategy_return_percent": round(
            strategy_return,
            2
        ),
        "buy_and_hold_return_percent": round(
            buy_hold_return,
            2
        ),
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate_percent": round(
            win_rate,
            2
        ),
        "paper_only": True,
        "note": (
            "Historical simulation only. "
            "Past performance does not guarantee "
            "future results."
        ),
    }


# ============================================================
# Startup
# ============================================================

@app.on_event("startup")
def startup_event():

    print("=" * 60)
    print(f"{APP_NAME} backend starting")
    print(f"Version: {VERSION}")
    print(f"Build: {BUILD_ID}")
    print("Mode: PAPER TRADING")
    print("Real-money trading: DISABLED")
    print("Market provider: CoinGecko")
    print("Signal engine: RSI + MA + Local History")
    print("=" * 60)


# ============================================================
# Local development
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
)
