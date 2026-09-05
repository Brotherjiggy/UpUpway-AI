# ============================================================
# UPUPWAY AI
# PROFESSIONAL PAPER-TRADING BACKEND
# FastAPI + resilient market-data caching
#
# PAPER TRADING ONLY — NO REAL-MONEY EXCHANGE CONNECTION
# ============================================================

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
import statistics
import time
import requests


# ============================================================
# CONFIGURATION
# ============================================================

APP_NAME = "UpUpway AI"
VERSION = "1.2.0"

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
REQUEST_TIMEOUT = 15

BTC_ID = "bitcoin"
ETH_ID = "ethereum"
SOL_ID = "solana"

# ------------------------------------------------------------
# MARKET DATA PROTECTION
# ------------------------------------------------------------

# Keep market data for 5 minutes.
# The frontend can poll every few seconds without causing
# a provider request every few seconds.
MARKET_CACHE_TTL = 300.0

# Never request CoinGecko more often than this.
PROVIDER_MIN_INTERVAL = 120.0

# Keep stale market data indefinitely as an emergency fallback.
STALE_MARKET_MAX_AGE = 86400.0

# Historical data can remain cached longer.
HISTORY_CACHE_TTL = 900.0

# Minimum interval between historical provider requests.
HISTORY_PROVIDER_MIN_INTERVAL = 600.0


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description=(
        "Professional AI market analysis and "
        "paper-trading backend."
    )
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# PAPER ACCOUNT
# ============================================================

paper_account: Dict[str, Any] = {
    "starting_balance": 10000.0,
    "cash": 10000.0,
    "btc": 0.0,
    "entry_price": None,
    "last_action": "NONE",
    "profit_loss": 0.0,
    "realized_profit_loss": 0.0,
    "unrealized_profit_loss": 0.0,
    "portfolio_value": 10000.0,
    "daily_start_value": 10000.0,
    "daily_start_date": None,
}


# ============================================================
# AUTO TRADING STATE
# ============================================================

auto_trading: Dict[str, Any] = {
    "enabled": False,
    "last_signal": "NONE",
    "last_action": "NONE",
    "last_price": None,
    "last_trade_time": None,
    "trades": 0,
    "wins": 0,
    "losses": 0,
    "consecutive_losses": 0,
}


# ============================================================
# RISK SETTINGS
# ============================================================

RISK_SETTINGS: Dict[str, float] = {
    "max_position_percent": 10.0,
    "minimum_confidence": 55.0,
    "trade_cooldown_seconds": 60.0,
    "stop_loss_percent": 3.0,
    "take_profit_percent": 6.0,
    "max_daily_loss_percent": 5.0,
    "max_consecutive_losses": 3,
}


# ============================================================
# TRADE HISTORY
# ============================================================

trade_history: List[Dict[str, Any]] = []


# ============================================================
# MARKET CACHE
# ============================================================

market_cache: Dict[str, Any] = {
    "data": None,
    "timestamp": 0.0,
    "provider_timestamp": 0.0,
    "last_attempt": 0.0,
    "last_error": None,
}


# ============================================================
# HISTORY CACHE
# ============================================================

history_cache: Dict[str, Any] = {}


# ============================================================
# REQUEST MODELS
# ============================================================

class PaperBuyRequest(BaseModel):
    confidence: Optional[float] = Field(
        default=None,
        ge=0,
        le=100
    )
    reason: Optional[str] = "Manual paper trade"


class PaperSellRequest(BaseModel):
    confidence: Optional[float] = Field(
        default=None,
        ge=0,
        le=100
    )
    reason: Optional[str] = "Manual paper trade"


class AutoTradingToggleRequest(BaseModel):
    enabled: bool


class BacktestRequest(BaseModel):
    asset: str = "BTC"
    strategy: str = "RSI_MA"
    initial_capital: float = Field(
        default=10000.0,
        gt=0
    )


# ============================================================
# GENERAL HELPERS
# ============================================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso() -> str:
    return utc_now().isoformat()


def safe_float(
    value: Any,
    default: float = 0.0
) -> float:

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(
    value: float,
    minimum: float,
    maximum: float
) -> float:

    return max(minimum, min(value, maximum))


# ============================================================
# PROVIDER REQUEST
# ============================================================

def coingecko_get(
    endpoint: str,
    params: Optional[Dict[str, Any]] = None
):

    url = f"{COINGECKO_BASE_URL}{endpoint}"

    try:

        response = requests.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
            headers={
                "Accept": "application/json",
                "User-Agent": (
                    "UpUpway-AI/1.2 "
                    "(paper-trading-platform)"
                )
            }
        )

        if response.status_code == 429:

            raise HTTPException(
                status_code=503,
                detail=(
                    "CoinGecko rate limit reached."
                )
            )

        response.raise_for_status()

        return response.json()

    except HTTPException:
        raise

    except requests.RequestException as exc:

        raise HTTPException(
            status_code=503,
            detail=(
                "Market data provider unavailable: "
                f"{str(exc)}"
            )
        )


# ============================================================
# FETCH MARKET DATA FROM COINGECKO
# ============================================================

def fetch_market_data_from_provider() -> Dict[str, Any]:

    data = coingecko_get(
        "/simple/price",
        {
            "ids": (
                f"{BTC_ID},{ETH_ID},{SOL_ID}"
            ),
            "vs_currencies": "usd",
            "include_24hr_change": "true",
        }
    )

    btc = data.get(BTC_ID, {})
    eth = data.get(ETH_ID, {})
    sol = data.get(SOL_ID, {})

    btc_price = safe_float(
        btc.get("usd")
    )

    eth_price = safe_float(
        eth.get("usd")
    )

    sol_price = safe_float(
        sol.get("usd")
    )

    if btc_price <= 0:

        raise HTTPException(
            status_code=503,
            detail=(
                "Bitcoin market price is unavailable."
            )
        )

    return {
        "symbol": "BTCUSDT",

        "price": btc_price,

        "change_24h": safe_float(
            btc.get("usd_24h_change")
        ),

        "volume_24h": None,

        "source": "CoinGecko",

        "eth_price": eth_price,

        "eth_change_24h": safe_float(
            eth.get("usd_24h_change")
        ),

        "sol_price": sol_price,

        "sol_change_24h": safe_float(
            sol.get("usd_24h_change")
        ),

        "updated_at": utc_iso(),
    }


# ============================================================
# RESILIENT MARKET DATA
# ============================================================

def get_market_data() -> Dict[str, Any]:

    now = time.time()

    cached = market_cache.get("data")

    cached_at = safe_float(
        market_cache.get("timestamp"),
        0.0
    )

    last_attempt = safe_float(
        market_cache.get("last_attempt"),
        0.0
    )

    cache_age = (
        now - cached_at
        if cached
        else None
    )

    provider_age = (
        now - last_attempt
        if last_attempt > 0
        else None
    )

    # --------------------------------------------------------
    # 1. Fresh cache
    # --------------------------------------------------------

    if (
        cached
        and cache_age is not None
        and cache_age < MARKET_CACHE_TTL
    ):

        result = dict(cached)

        result["cache"] = "HIT"

        result["cache_age_seconds"] = round(
            cache_age,
            2
        )

        result["provider_request"] = False

        return result

    # --------------------------------------------------------
    # 2. Provider request cooldown
    # --------------------------------------------------------

    if (
        last_attempt > 0
        and provider_age is not None
        and provider_age < PROVIDER_MIN_INTERVAL
    ):

        if cached:

            result = dict(cached)

            result["source"] = (
                "CoinGecko cached fallback"
            )

            result["cache"] = (
                "PROVIDER_COOLDOWN"
            )

            result["cache_age_seconds"] = round(
                cache_age,
                2
            )

            result["provider_request"] = False

            return result

    # --------------------------------------------------------
    # 3. Attempt provider refresh
    # --------------------------------------------------------

    market_cache["last_attempt"] = now

    try:

        fresh = fetch_market_data_from_provider()

        market_cache["data"] = fresh
        market_cache["timestamp"] = now
        market_cache["provider_timestamp"] = now
        market_cache["last_error"] = None

        result = dict(fresh)

        result["cache"] = "MISS"

        result["cache_age_seconds"] = 0.0

        result["provider_request"] = True

        return result

    except HTTPException as exc:

        market_cache["last_error"] = str(
            exc.detail
        )

        # ----------------------------------------------------
        # 4. Emergency stale fallback
        # ----------------------------------------------------

        if (
            cached
            and cache_age is not None
            and cache_age <= STALE_MARKET_MAX_AGE
        ):

            result = dict(cached)

            result["source"] = (
                "CoinGecko stale cached fallback"
            )

            result["cache"] = (
                "STALE_FALLBACK"
            )

            result["cache_age_seconds"] = round(
                cache_age,
                2
            )

            result["provider_request"] = True

            result["provider_error"] = str(
                exc.detail
            )

            return result

        # ----------------------------------------------------
        # 5. No usable data
        # ----------------------------------------------------

        raise


# ============================================================
# BTC HISTORY
# ============================================================

def get_price_history(
    days: int = 1,
    interval: Optional[str] = None
) -> List[float]:

    cache_key = str(days)

    now = time.time()

    cached = history_cache.get(
        cache_key
    )

    # --------------------------------------------------------
    # Fresh history cache
    # --------------------------------------------------------

    if cached:

        age = (
            now
            - safe_float(
                cached.get("timestamp"),
                0.0
            )
        )

        if age < HISTORY_CACHE_TTL:

            return list(
                cached["prices"]
            )

    # --------------------------------------------------------
    # Historical provider cooldown
    # --------------------------------------------------------

    if cached:

        last_attempt = safe_float(
            cached.get("last_attempt"),
            0.0
        )

        if last_attempt > 0:

            elapsed = now - last_attempt

            if elapsed < HISTORY_PROVIDER_MIN_INTERVAL:

                return list(
                    cached["prices"]
                )

    # --------------------------------------------------------
    # Prepare cache record
    # --------------------------------------------------------

    if cache_key not in history_cache:

        history_cache[cache_key] = {
            "prices": [],
            "timestamp": 0.0,
            "last_attempt": 0.0,
            "last_error": None,
        }

    history_cache[cache_key][
        "last_attempt"
    ] = now

    params = {
        "vs_currency": "usd",
        "days": days,
    }

    try:

        data = coingecko_get(
            f"/coins/{BTC_ID}/market_chart",
            params
        )

        prices = []

        for item in data.get(
            "prices",
            []
        ):

            if (
                isinstance(item, list)
                and len(item) >= 2
            ):

                price = safe_float(
                    item[1]
                )

                if price > 0:
                    prices.append(price)

        if len(prices) < 20:

            raise HTTPException(
                status_code=503,
                detail=(
                    "Not enough historical BTC "
                    "data for analysis."
                )
            )

        history_cache[cache_key] = {
            "prices": prices,
            "timestamp": now,
            "last_attempt": now,
            "last_error": None,
        }

        return prices

    except HTTPException as exc:

        history_cache[cache_key][
            "last_error"
        ] = str(exc.detail)

        # Use existing historical cache if available.
        if cached and cached.get("prices"):

            return list(
                cached["prices"]
            )

        raise


# ============================================================
# MOVING AVERAGE
# ============================================================

def moving_average(
    prices: List[float],
    period: int
) -> Optional[float]:

    if len(prices) < period:
        return None

    return statistics.mean(
        prices[-period:]
    )


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    prices: List[float],
    period: int = 14
) -> Optional[float]:

    if len(prices) <= period:
        return None

    gains = []
    losses = []

    for i in range(1, len(prices)):

        change = (
            prices[i]
            - prices[i - 1]
        )

        if change >= 0:

            gains.append(change)
            losses.append(0.0)

        else:

            gains.append(0.0)
            losses.append(
                abs(change)
            )

    average_gain = statistics.mean(
        gains[-period:]
    )

    average_loss = statistics.mean(
        losses[-period:]
    )

    if average_loss == 0:

        return (
            100.0
            if average_gain > 0
            else 50.0
        )

    rs = (
        average_gain
        / average_loss
    )

    return round(
        clamp(
            100 - (
                100 / (1 + rs)
            ),
            0,
            100
        ),
        2
    )


# ============================================================
# SIGNAL ENGINE
# ============================================================

def generate_signal() -> Dict[str, Any]:

    prices = get_price_history(
        days=1
    )

    current_price = prices[-1]

    rsi = calculate_rsi(
        prices,
        14
    )

    short_ma = moving_average(
        prices,
        5
    )

    long_ma = moving_average(
        prices,
        14
    )

    if (
        rsi is None
        or short_ma is None
        or long_ma is None
    ):

        raise HTTPException(
            status_code=503,
            detail=(
                "Not enough data to generate "
                "a trading signal."
            )
        )

    score = 50.0

    reasons = []

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    if rsi < 30:

        score += 20

        reasons.append(
            "RSI indicates oversold conditions."
        )

    elif rsi < 40:

        score += 10

        reasons.append(
            "RSI shows relatively weak momentum."
        )

    elif rsi > 70:

        score -= 20

        reasons.append(
            "RSI indicates overbought conditions."
        )

    elif rsi > 60:

        score -= 10

        reasons.append(
            "RSI shows strong but potentially "
            "extended momentum."
        )

    else:

        reasons.append(
            "RSI is in a neutral range."
        )

    # --------------------------------------------------------
    # Moving averages
    # --------------------------------------------------------

    if short_ma > long_ma:

        score += 20

        reasons.append(
            "Short-term moving average is above "
            "long-term average."
        )

    elif short_ma < long_ma:

        score -= 20

        reasons.append(
            "Short-term moving average is below "
            "long-term average."
        )

    else:

        reasons.append(
            "Moving averages are approximately equal."
        )

    # --------------------------------------------------------
    # Price vs long MA
    # --------------------------------------------------------

    if current_price > long_ma:

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
        and current_price > long_ma
    ):

        trend = "BULLISH"

    elif (
        short_ma < long_ma
        and current_price < long_ma
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

        "rsi": round(
            rsi,
            2
        ),

        "price": round(
            current_price,
            2
        ),

        "short_ma": round(
            short_ma,
            2
        ),

        "long_ma": round(
            long_ma,
            2
        ),

        "source": (
            "UpUpway AI rule-based "
            "signal engine"
        ),

        "paper_only": True,

        "generated_at": utc_iso(),
    }


# ============================================================
# DAILY RISK
# ============================================================

def ensure_daily_baseline(
    current_value: float
) -> None:

    today = (
        utc_now()
        .date()
        .isoformat()
    )

    if (
        paper_account[
            "daily_start_date"
        ]
        != today
    ):

        paper_account[
            "daily_start_date"
        ] = today

        paper_account[
            "daily_start_value"
        ] = current_value


def daily_loss_percent(
    current_value: float
) -> float:

    ensure_daily_baseline(
        current_value
    )

    baseline = paper_account[
        "daily_start_value"
    ]

    if baseline <= 0:
        return 0.0

    loss = (
        (
            baseline
            - current_value
        )
        / baseline
    ) * 100

    return round(
        max(
            0.0,
            loss
        ),
        2
    )


def max_daily_loss_reached(
    current_value: float
) -> bool:

    return (
        daily_loss_percent(
            current_value
        )
        >= RISK_SETTINGS[
            "max_daily_loss_percent"
        ]
    )


def max_consecutive_losses_reached() -> bool:

    return (
        auto_trading[
            "consecutive_losses"
        ]
        >= RISK_SETTINGS[
            "max_consecutive_losses"
        ]
    )


# ============================================================
# ACCOUNT VALUE
# ============================================================

def calculate_account_value(
    current_price: Optional[float] = None
):

    if current_price is None:

        try:

            current_price = get_market_data()[
                "price"
            ]

        except Exception:

            current_price = 0.0

    btc_value = (
        paper_account["btc"]
        * current_price
    )

    portfolio_value = (
        paper_account["cash"]
        + btc_value
    )

    unrealized = 0.0

    if (
        paper_account["btc"] > 0
        and paper_account["entry_price"]
        is not None
    ):

        unrealized = (
            current_price
            - paper_account["entry_price"]
        ) * paper_account["btc"]

    realized = paper_account[
        "realized_profit_loss"
    ]

    total_profit_loss = (
        realized
        + unrealized
    )

    paper_account[
        "unrealized_profit_loss"
    ] = round(
        unrealized,
        2
    )

    paper_account[
        "profit_loss"
    ] = round(
        total_profit_loss,
        2
    )

    paper_account[
        "portfolio_value"
    ] = round(
        portfolio_value,
        2
    )

    ensure_daily_baseline(
        portfolio_value
    )

    return {
        "starting_balance": round(
            paper_account[
                "starting_balance"
            ],
            2
        ),

        "cash": round(
            paper_account["cash"],
            2
        ),

        "btc": round(
            paper_account["btc"],
            8
        ),

        "btc_price": round(
            current_price,
            2
        ),

        "btc_value": round(
            btc_value,
            2
        ),

        "entry_price": (
            round(
                paper_account[
                    "entry_price"
                ],
                2
            )
            if paper_account[
                "entry_price"
            ] is not None
            else None
        ),

        "profit_loss": round(
            total_profit_loss,
            2
        ),

        "realized_profit_loss": round(
            realized,
            2
        ),

        "unrealized_profit_loss": round(
            unrealized,
            2
        ),

        "portfolio_value": round(
            portfolio_value,
            2
        ),

        "daily_loss_percent":
            daily_loss_percent(
                portfolio_value
            ),

        "last_action":
            paper_account[
                "last_action"
            ],

        "paper_mode": True,
    }


# ============================================================
# POSITION SIZE
# ============================================================

def calculate_buy_amount() -> float:

    percent = RISK_SETTINGS[
        "max_position_percent"
    ]

    return max(
        0.0,
        paper_account["cash"]
        * percent
        / 100
    )


# ============================================================
# COOLDOWN
# ============================================================

def cooldown_active() -> bool:

    last_trade = auto_trading[
        "last_trade_time"
    ]

    if not last_trade:
        return False

    try:

        previous = datetime.fromisoformat(
            last_trade
        )

        elapsed = (
            utc_now()
            - previous
        ).total_seconds()

        return (
            elapsed
            < RISK_SETTINGS[
                "trade_cooldown_seconds"
            ]
        )

    except (
        ValueError,
        TypeError
    ):

        return False


def cooldown_remaining() -> float:

    last_trade = auto_trading[
        "last_trade_time"
    ]

    if not last_trade:
        return 0.0

    try:

        previous = datetime.fromisoformat(
            last_trade
        )

        elapsed = (
            utc_now()
            - previous
        ).total_seconds()

        return max(
            0.0,
            round(
                RISK_SETTINGS[
                    "trade_cooldown_seconds"
                ]
                - elapsed,
                2
            )
        )

    except (
        ValueError,
        TypeError
    ):

        return 0.0


# ============================================================
# TRADE RECORDING
# ============================================================

def record_trade(
    action: str,
    price: float,
    quantity: float,
    confidence: Optional[float],
    reason: str,
    pnl: Optional[float] = None,
    status: str = "OPEN",
) -> Dict[str, Any]:

    trade = {

        "id": len(
            trade_history
        ) + 1,

        "timestamp": utc_iso(),

        "action": action,

        "symbol": "BTCUSDT",

        "price": round(
            price,
            2
        ),

        "quantity": round(
            quantity,
            8
        ),

        "confidence": (
            round(
                confidence,
                2
            )
            if confidence is not None
            else None
        ),

        "reason": reason,

        "pnl": (
            round(
                pnl,
                2
            )
            if pnl is not None
            else None
        ),

        "status": status,

        "paper_trade": True,
    }

    trade_history.append(
        trade
    )

    return trade


# ============================================================
# PAPER BUY
# ============================================================

def execute_paper_buy(
    confidence: Optional[float] = None,
    reason: str = "Manual paper trade"
) -> Dict[str, Any]:

    if paper_account["btc"] > 0:

        raise HTTPException(
            status_code=400,
            detail=(
                "BUY blocked: a BTC paper "
                "position is already open."
            )
        )

    account = calculate_account_value()

    if max_daily_loss_reached(
        account["portfolio_value"]
    ):

        raise HTTPException(
            status_code=400,
            detail=(
                "BUY blocked: maximum daily "
                "loss limit has been reached."
            )
        )

    market = get_market_data()

    price = market["price"]

    cash_to_use = calculate_buy_amount()

    if cash_to_use <= 0:

        raise HTTPException(
            status_code=400,
            detail=(
                "BUY blocked: insufficient "
                "paper cash."
            )
        )

    quantity = (
        cash_to_use
        / price
    )

    paper_account["cash"] -= (
        cash_to_use
    )

    paper_account["btc"] = (
        quantity
    )

    paper_account[
        "entry_price"
    ] = price

    paper_account[
        "last_action"
    ] = "BUY"

    trade = record_trade(
        action="BUY",
        price=price,
        quantity=quantity,
        confidence=confidence,
        reason=reason,
        status="OPEN",
    )

    return {
        "success": True,

        "message": (
            "Paper BUY executed successfully."
        ),

        "trade": trade,

        "account":
            calculate_account_value(
                price
            ),

        "risk":
            get_risk_snapshot(
                price
            ),
    }


# ============================================================
# PAPER SELL
# ============================================================

def execute_paper_sell(
    confidence: Optional[float] = None,
    reason: str = "Manual paper trade"
) -> Dict[str, Any]:

    if paper_account["btc"] <= 0:

        raise HTTPException(
            status_code=400,
            detail=(
                "SELL blocked: no BTC paper "
                "position is open."
            )
        )

    market = get_market_data()

    price = market["price"]

    quantity = paper_account[
        "btc"
    ]

    entry_price = paper_account[
        "entry_price"
    ]

    if entry_price is None:

        raise HTTPException(
            status_code=500,
            detail=(
                "Paper position has no "
                "entry price."
            )
        )

    sale_value = (
        quantity
        * price
    )

    pnl = (
        price
        - entry_price
    ) * quantity

    paper_account["cash"] += (
        sale_value
    )

    paper_account["btc"] = 0.0

    paper_account[
        "entry_price"
    ] = None

    paper_account[
        "last_action"
    ] = "SELL"

    paper_account[
        "realized_profit_loss"
    ] += pnl

    trade = record_trade(
        action="SELL",
        price=price,
        quantity=quantity,
        confidence=confidence,
        reason=reason,
        pnl=pnl,
        status="CLOSED",
    )

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
        ] += 1

    return {
        "success": True,

        "message": (
            "Paper SELL executed successfully."
        ),

        "trade": trade,

        "account":
            calculate_account_value(
                price
            ),

        "risk":
            get_risk_snapshot(
                price
            ),
    }


# ============================================================
# STOP LOSS / TAKE PROFIT
# ============================================================

def position_exit_reason(
    price: float
) -> Optional[str]:

    entry = paper_account[
        "entry_price"
    ]

    if (
        paper_account["btc"] <= 0
        or entry is None
    ):

        return None

    change_percent = (
        (
            price
            - entry
        )
        / entry
    ) * 100

    if (
        change_percent
        <= -RISK_SETTINGS[
            "stop_loss_percent"
        ]
    ):

        return (
            "Stop-loss triggered at "
            f"{change_percent:.2f}%."
        )

    if (
        change_percent
        >= RISK_SETTINGS[
            "take_profit_percent"
        ]
    ):

        return (
            "Take-profit triggered at "
            f"{change_percent:.2f}%."
        )

    return None


# ============================================================
# RISK SNAPSHOT
# ============================================================

def get_risk_snapshot(
    current_price: Optional[float] = None
) -> Dict[str, Any]:

    account = calculate_account_value(
        current_price
    )

    entry = paper_account[
        "entry_price"
    ]

    position_change = None

    if (
        entry is not None
        and account["btc"] > 0
    ):

        position_change = round(
            (
                (
                    account["btc_price"]
                    - entry
                )
                / entry
            ) * 100,
            2
        )

    return {

        "max_position_percent":
            RISK_SETTINGS[
                "max_position_percent"
            ],

        "minimum_confidence":
            RISK_SETTINGS[
                "minimum_confidence"
            ],

        "trade_cooldown_seconds":
            RISK_SETTINGS[
                "trade_cooldown_seconds"
            ],

        "stop_loss_percent":
            RISK_SETTINGS[
                "stop_loss_percent"
            ],

        "take_profit_percent":
            RISK_SETTINGS[
                "take_profit_percent"
            ],

        "max_daily_loss_percent":
            RISK_SETTINGS[
                "max_daily_loss_percent"
            ],

        "max_consecutive_losses":
            RISK_SETTINGS[
                "max_consecutive_losses"
            ],

        "daily_loss_percent":
            account[
                "daily_loss_percent"
            ],

        "daily_loss_limit_reached":
            max_daily_loss_reached(
                account[
                    "portfolio_value"
                ]
            ),

        "consecutive_losses":
            auto_trading[
                "consecutive_losses"
            ],

        "consecutive_loss_limit_reached":
            max_consecutive_losses_reached(),

        "position_open":
            paper_account["btc"] > 0,

        "position_change_percent":
            position_change,

        "paper_only": True,
    }


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():

    return {

        "name": APP_NAME,

        "version": VERSION,

        "status": "online",

        "mode": "paper",

        "description": (
            "AI crypto market analysis "
            "and paper trading backend."
        ),

        "paper_trading_only": True,

        "real_money_trading": False,

        "timestamp": utc_iso(),
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

        "mode": "paper",

        "timestamp": utc_iso(),
    }


# ============================================================
# SYSTEM STATUS
# ============================================================

@app.get("/api/status")
def system_status():

    cached = market_cache.get(
        "data"
    )

    cached_at = safe_float(
        market_cache.get(
            "timestamp"
        ),
        0.0
    )

    cache_age = (
        round(
            time.time()
            - cached_at,
            2
        )
        if cached
        else None
    )

    return {

        "name": APP_NAME,

        "version": VERSION,

        "status": "online",

        "mode": "paper",

        "market_data":
            "CoinGecko + resilient cache",

        "market_cache":
            "active",

        "market_cache_ttl":
            MARKET_CACHE_TTL,

        "market_cache_age":
            cache_age,

        "signal_engine":
            "RSI + Moving Average",

        "auto_trading":
            auto_trading[
                "enabled"
            ],

        "paper_trading": True,

        "real_money_trading": False,

        "api_keys_required": False,

        "timestamp": utc_iso(),
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
# PAPER ACCOUNT
# ============================================================

@app.get("/api/paper-account")
def get_paper_account():

    return calculate_account_value()


# ============================================================
# PAPER BUY
# ============================================================

@app.post("/api/paper-buy")
def paper_buy(
    request: PaperBuyRequest
):

    return execute_paper_buy(
        confidence=request.confidence,
        reason=(
            request.reason
            or "Manual paper trade"
        )
    )


# ============================================================
# PAPER SELL
# ============================================================

@app.post("/api/paper-sell")
def paper_sell(
    request: PaperSellRequest
):

    return execute_paper_sell(
        confidence=request.confidence,
        reason=(
            request.reason
            or "Manual paper trade"
        )
    )


# ============================================================
# AUTO TRADING STATUS
# ============================================================

@app.get("/api/auto-trading")
def get_auto_trading():

    account = calculate_account_value()

    return {

        "enabled":
            auto_trading[
                "enabled"
            ],

        "last_signal":
            auto_trading[
                "last_signal"
            ],

        "last_action":
            auto_trading[
                "last_action"
            ],

        "last_price":
            auto_trading[
                "last_price"
            ],

        "last_trade_time":
            auto_trading[
                "last_trade_time"
            ],

        "trades":
            auto_trading[
                "trades"
            ],

        "wins":
            auto_trading[
                "wins"
            ],

        "losses":
            auto_trading[
                "losses"
            ],

        "consecutive_losses":
            auto_trading[
                "consecutive_losses"
            ],

        "risk_settings":
            RISK_SETTINGS,

        "cooldown_active":
            cooldown_active(),

        "cooldown_remaining":
            cooldown_remaining(),

        "position_open":
            paper_account[
                "btc"
            ] > 0,

        "paper_account":
            account,

        "risk":
            get_risk_snapshot(
                account[
                    "btc_price"
                ]
            ),

        "paper_only": True,
    }


# ============================================================
# AUTO TRADING TOGGLE
# ============================================================

@app.post("/api/auto-trading/toggle")
def toggle_auto_trading(
    request: AutoTradingToggleRequest
):

    if request.enabled:

        account = calculate_account_value()

        if max_daily_loss_reached(
            account[
                "portfolio_value"
            ]
        ):

            raise HTTPException(
                status_code=400,
                detail=(
                    "Auto trading cannot "
                    "be enabled because "
                    "the maximum daily loss "
                    "limit has been reached."
                )
            )

        if max_consecutive_losses_reached():

            raise HTTPException(
                status_code=400,
                detail=(
                    "Auto trading cannot "
                    "be enabled because "
                    "the maximum consecutive-"
                    "loss limit has been reached."
                )
            )

    auto_trading[
        "enabled"
    ] = request.enabled

    return {

        "success": True,

        "enabled":
            auto_trading[
                "enabled"
            ],

        "message": (
            "Auto trading enabled."
            if request.enabled
            else "Auto trading disabled."
        ),

        "mode": "paper",

        "paper_only": True,
    }


# ============================================================
# AUTO TRADING RUN
# ============================================================

@app.post("/api/auto-trading/run")
def run_auto_trading():

    if not auto_trading[
        "enabled"
    ]:

        return {

            "success": False,

            "executed": False,

            "message":
                "Auto trading is disabled.",

            "enabled": False,

            "paper_only": True,
        }

    account = calculate_account_value()

    # --------------------------------------------------------
    # Daily loss protection
    # --------------------------------------------------------

    if max_daily_loss_reached(
        account[
            "portfolio_value"
        ]
    ):

        auto_trading[
            "enabled"
        ] = False

        return {

            "success": False,

            "executed": False,

            "action": "RISK_STOP",

            "message": (
                "Auto trading stopped: "
                "maximum daily loss limit reached."
            ),

            "risk":
                get_risk_snapshot(
                    account[
                        "btc_price"
                    ]
                ),

            "paper_only": True,
        }

    # --------------------------------------------------------
    # Consecutive-loss protection
    # --------------------------------------------------------

    if max_consecutive_losses_reached():

        auto_trading[
            "enabled"
        ] = False

        return {

            "success": False,

            "executed": False,

            "action": "RISK_STOP",

            "message": (
                "Auto trading stopped: "
                "maximum consecutive losses reached."
            ),

            "risk":
                get_risk_snapshot(
                    account[
                        "btc_price"
                    ]
                ),

            "paper_only": True,
        }

    # --------------------------------------------------------
    # Existing position management
    # --------------------------------------------------------

    if paper_account["btc"] > 0:

        market = get_market_data()

        current_price = market[
            "price"
        ]

        exit_reason = (
            position_exit_reason(
                current_price
            )
        )

        if exit_reason:

            result = execute_paper_sell(
                confidence=100.0,
                reason=exit_reason
            )

            auto_trading[
                "last_action"
            ] = "SELL"

            auto_trading[
                "last_price"
            ] = current_price

            auto_trading[
                "last_trade_time"
            ] = utc_iso()

            auto_trading[
                "trades"
            ] += 1

            return {

                "success": True,

                "executed": True,

                "action": "SELL",

                "confidence": 100.0,

                "message": exit_reason,

                "result": result,

                "paper_only": True,
            }

    # --------------------------------------------------------
    # Cooldown
    # --------------------------------------------------------

    if cooldown_active():

        return {

            "success": True,

            "executed": False,

            "action": "COOLDOWN",

            "message": (
                "Auto trading is waiting "
                "for the configured cooldown."
            ),

            "cooldown_remaining":
                cooldown_remaining(),

            "paper_only": True,
        }

    # --------------------------------------------------------
    # Generate signal
    # --------------------------------------------------------

    analysis = generate_signal()

    action = analysis[
        "action"
    ]

    confidence = analysis[
        "confidence"
    ]

    price = analysis[
        "price"
    ]

    auto_trading[
        "last_signal"
    ] = action

    auto_trading[
        "last_price"
    ] = price

    # --------------------------------------------------------
    # Confidence filter
    # --------------------------------------------------------

    if confidence < RISK_SETTINGS[
        "minimum_confidence"
    ]:

        return {

            "success": True,

            "executed": False,

            "action": action,

            "confidence": confidence,

            "message": (
                "Signal rejected by "
                "minimum-confidence "
                "risk filter."
            ),

            "minimum_confidence":
                RISK_SETTINGS[
                    "minimum_confidence"
                ],

            "paper_only": True,
        }

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    if action == "BUY":

        if paper_account["btc"] > 0:

            return {

                "success": True,

                "executed": False,

                "action": "BUY",

                "confidence": confidence,

                "message": (
                    "BUY blocked because "
                    "a BTC position is "
                    "already open."
                ),

                "paper_only": True,
            }

        result = execute_paper_buy(
            confidence=confidence,
            reason=analysis[
                "description"
            ]
        )

        auto_trading[
            "last_action"
        ] = "BUY"

        auto_trading[
            "last_trade_time"
        ] = utc_iso()

        auto_trading[
            "trades"
        ] += 1

        return {

            "success": True,

            "executed": True,

            "action": "BUY",

            "confidence": confidence,

            "message":
                "AI paper BUY executed.",

            "result": result,

            "paper_only": True,
        }

    # --------------------------------------------------------
    # SELL
    # --------------------------------------------------------

    if action == "SELL":

        if paper_account["btc"] <= 0:

            return {

                "success": True,

                "executed": False,

                "action": "SELL",

                "confidence": confidence,

                "message": (
                    "SELL signal received, "
                    "but there is no BTC "
                    "position."
                ),

                "paper_only": True,
            }

        result = execute_paper_sell(
            confidence=confidence,
            reason=analysis[
                "description"
            ]
        )

        auto_trading[
            "last_action"
        ] = "SELL"

        auto_trading[
            "last_trade_time"
        ] = utc_iso()

        auto_trading[
            "trades"
        ] += 1

        return {

            "success": True,

            "executed": True,

            "action": "SELL",

            "confidence": confidence,

            "message":
                "AI paper SELL executed.",

            "result": result,

            "paper_only": True,
        }

    # --------------------------------------------------------
    # HOLD
    # --------------------------------------------------------

    auto_trading[
        "last_action"
    ] = "HOLD"

    return {

        "success": True,

        "executed": False,

        "action": "HOLD",

        "confidence": confidence,

        "message": (
            "AI recommends HOLD. "
            "No paper trade executed."
        ),

        "analysis": analysis,

        "paper_only": True,
    }


# ============================================================
# TRADES
# ============================================================

@app.get("/api/trades")
def get_trades():

    return {

        "success": True,

        "count":
            len(trade_history),

        "trades":
            list(
                reversed(
                    trade_history
                )
            ),

        "paper_only": True,
    }


# ============================================================
# RISK SETTINGS
# ============================================================

@app.get("/api/risk-settings")
def get_risk_settings():

    return {

        "success": True,

        "risk_settings":
            RISK_SETTINGS,

        "risk":
            get_risk_snapshot(),

        "paper_only": True,
    }


# ============================================================
# RESET
# ============================================================

@app.post("/api/paper-account/reset")
def reset_paper_account():

    paper_account[
        "cash"
    ] = paper_account[
        "starting_balance"
    ]

    paper_account[
        "btc"
    ] = 0.0

    paper_account[
        "entry_price"
    ] = None

    paper_account[
        "last_action"
    ] = "NONE"

    paper_account[
        "profit_loss"
    ] = 0.0

    paper_account[
        "realized_profit_loss"
    ] = 0.0

    paper_account[
        "unrealized_profit_loss"
    ] = 0.0

    paper_account[
        "portfolio_value"
    ] = paper_account[
        "starting_balance"
    ]

    paper_account[
        "daily_start_value"
    ] = paper_account[
        "starting_balance"
    ]

    paper_account[
        "daily_start_date"
    ] = (
        utc_now()
        .date()
        .isoformat()
    )

    trade_history.clear()

    auto_trading[
        "enabled"
    ] = False

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

    auto_trading[
        "consecutive_losses"
    ] = 0

    return {

        "success": True,

        "message":
            "Paper account reset successfully.",

        "account":
            calculate_account_value(),

        "paper_only": True,
    }


# ============================================================
# BACKTEST
# ============================================================

def run_backtest(
    initial_capital: float,
    strategy: str
) -> Dict[str, Any]:

    prices = get_price_history(
        days=30
    )

    if len(prices) < 20:

        raise HTTPException(
            status_code=503,
            detail=(
                "Not enough historical "
                "data for backtesting."
            )
        )

    cash = initial_capital

    btc = 0.0

    entry_price = None

    completed_trades = []

    wins = 0

    losses = 0

    strategy_name = strategy.upper()

    for index in range(
        14,
        len(prices)
    ):

        window = prices[
            :index + 1
        ]

        current_price = window[
            -1
        ]

        rsi = calculate_rsi(
            window,
            14
        )

        short_ma = moving_average(
            window,
            5
        )

        long_ma = moving_average(
            window,
            14
        )

        if (
            rsi is None
            or short_ma is None
            or long_ma is None
        ):

            continue

        action = "HOLD"

        if strategy_name in {
            "RSI_MA",
            "RSI+MA",
            "DEFAULT"
        }:

            if (
                rsi < 35
                and short_ma > long_ma
                and btc == 0
            ):

                action = "BUY"

            elif (
                rsi > 65
                and btc > 0
            ):

                action = "SELL"

        elif strategy_name in {
            "MA",
            "MOVING_AVERAGE"
        }:

            if (
                short_ma > long_ma
                and btc == 0
            ):

                action = "BUY"

            elif (
                short_ma < long_ma
                and btc > 0
            ):

                action = "SELL"

        if (
            action == "BUY"
            and btc == 0
        ):

            allocation = (
                cash
                * (
                    RISK_SETTINGS[
                        "max_position_percent"
                    ]
                    / 100
                )
            )

            if allocation > 0:

                btc = (
                    allocation
                    / current_price
                )

                cash -= allocation

                entry_price = (
                    current_price
                )

        elif (
            action == "SELL"
            and btc > 0
        ):

            proceeds = (
                btc
                * current_price
            )

            pnl = (
                current_price
                - entry_price
            ) * btc

            if pnl > 0:

                wins += 1

            elif pnl < 0:

                losses += 1

            completed_trades.append({

                "entry_price":
                    round(
                        entry_price,
                        2
                    ),

                "exit_price":
                    round(
                        current_price,
                        2
                    ),

                "quantity":
                    round(
                        btc,
                        8
                    ),

                "pnl":
                    round(
                        pnl,
                        2
                    ),
            })

            cash += proceeds

            btc = 0.0

            entry_price = None

    final_price = prices[-1]

    final_value = (
        cash
        + (
            btc
            * final_price
        )
    )

    total_return = (
        (
            final_value
            - initial_capital
        )
        / initial_capital
    ) * 100

    total_closed = (
        wins
        + losses
    )

    win_rate = (
        (
            wins
            / total_closed
        ) * 100
        if total_closed > 0
        else 0.0
    )

    buy_hold_value = (
        initial_capital
        / prices[0]
    ) * final_price

    buy_hold_return = (
        (
            buy_hold_value
            - initial_capital
        )
        / initial_capital
    ) * 100

    return {

        "success": True,

        "asset": "BTC",

        "strategy": strategy_name,

        "initial_capital":
            round(
                initial_capital,
                2
            ),

        "final_value":
            round(
                final_value,
                2
            ),

        "total_return":
            round(
                total_return,
                2
            ),

        "return":
            round(
                total_return,
                2
            ),

        "buy_and_hold_return":
            round(
                buy_hold_return,
                2
            ),

        "win_rate":
            round(
                win_rate,
                2
            ),

        "winning_trades":
            wins,

        "losing_trades":
            losses,

        "closed_trades":
            total_closed,

        "open_position":
            btc > 0,

        "historical_data_points":
            len(prices),

        "paper_simulation":
            True,

        "generated_at":
            utc_iso(),
    }


# ============================================================
# BACKTEST ENDPOINT
# ============================================================

@app.post("/api/backtest")
def backtest(
    request: BacktestRequest
):

    if request.asset.upper() != "BTC":

        raise HTTPException(
            status_code=400,
            detail=(
                "The current backtest engine "
                "supports BTC only."
            )
        )

    return run_backtest(
        initial_capital=
            request.initial_capital,

        strategy=
            request.strategy
    )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup_event():

    print("=" * 60)

    print("UPUPWAY AI BACKEND")

    print("=" * 60)

    print(
        f"Version: {VERSION}"
    )

    print(
        "Status: ONLINE"
    )

    print(
        "Mode: PAPER TRADING"
    )

    print(
        "Market Data: "
        "CoinGecko + resilient cache"
    )

    print(
        "Market Cache TTL: "
        f"{MARKET_CACHE_TTL}s"
    )

    print(
        "Provider Min Interval: "
        f"{PROVIDER_MIN_INTERVAL}s"
    )

    print(
        "Signal Engine: "
        "RSI + Moving Average"
    )

    print(
        "Risk Engine: "
        "Position + SL + TP + Daily Loss"
    )

    print(
        "Real Money Trading: DISABLED"
    )

    print("=" * 60)


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
)
