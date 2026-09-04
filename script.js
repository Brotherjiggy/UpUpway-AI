/* =========================================================
   UPUPWAY AI
   FRONTEND ENGINE
   PAPER TRADING ONLY
========================================================= */


/* =========================================================
   API CONFIGURATION
========================================================= */

const API_BASE_URL = "https://upupway-ai.onrender.com";


/* =========================================================
   GLOBAL STATE
========================================================= */

let currentMarket = null;
let currentSignal = null;
let autoTradingEnabled = false;


/* =========================================================
   DOM HELPER
========================================================= */

function $(id) {
    return document.getElementById(id);
}


/* =========================================================
   API HELPER
========================================================= */

async function apiRequest(endpoint, options = {}) {

    const url = `${API_BASE_URL}${endpoint}`;

    try {

        const response = await fetch(url, {
            ...options,

            headers: {
                "Content-Type": "application/json",
                ...(options.headers || {})
            }
        });

        const text = await response.text();

        let data = {};

        try {
            data = text ? JSON.parse(text) : {};
        } catch {
            data = {
                raw: text
            };
        }

        if (!response.ok) {

            const message =
                data.detail ||
                data.message ||
                data.error ||
                data.raw ||
                `HTTP ${response.status}`;

            throw new Error(message);
        }

        return data;

    } catch (error) {

        console.error(
            `UpUpway API error: ${endpoint}`,
            error
        );

        throw error;
    }
}


/* =========================================================
   FORMATTERS
========================================================= */

function formatCurrency(value) {

    const number = Number(value);

    if (!Number.isFinite(number)) {
        return "$0.00";
    }

    return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 2
    }).format(number);
}


function formatNumber(value, decimals = 6) {

    const number = Number(value);

    if (!Number.isFinite(number)) {
        return "0";
    }

    return number.toLocaleString("en-US", {
        maximumFractionDigits: decimals
    });
}


function formatPercent(value) {

    const number = Number(value);

    if (!Number.isFinite(number)) {
        return "0.00%";
    }

    return `${number >= 0 ? "+" : ""}${number.toFixed(2)}%`;
}


function formatTime() {

    return new Date().toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit"
    });
}


/* =========================================================
   CONNECTION STATUS
========================================================= */

function setConnectionStatus(online, message) {

    const element = $("connectionStatus");

    if (!element) {
        return;
    }

    element.textContent = message;

    element.classList.remove(
        "connection-online",
        "connection-offline"
    );

    element.classList.add(
        online
            ? "connection-online"
            : "connection-offline"
    );
}


/* =========================================================
   MOBILE MENU
========================================================= */

function setupMobileMenu() {

    const menuButton = $("menuButton");
    const nav = $("mainNav");

    if (!menuButton || !nav) {
        return;
    }

    menuButton.addEventListener("click", () => {

        nav.classList.toggle("open");

    });


    nav.querySelectorAll("a").forEach(link => {

        link.addEventListener("click", () => {

            nav.classList.remove("open");

        });

    });
}


/* =========================================================
   MARKET DATA
========================================================= */

async function fetchMarket() {

    try {

        const data = await apiRequest("/api/market");

        currentMarket = data;

        updateMarketUI(data);

        setConnectionStatus(
            true,
            "AI engine connected"
        );

    } catch (error) {

        setConnectionStatus(
            false,
            `Backend unavailable: ${error.message}`
        );

        console.error(
            "Market request failed:",
            error
        );
    }
}


function updateMarketUI(data) {

    const price =
        Number(data.price);

    const change =
        Number(data.change_24h);


    /* BTC */

    if ($("btcPrice")) {

        $("btcPrice").textContent =
            formatCurrency(price);

    }


    if ($("btcChange")) {

        $("btcChange").textContent =
            formatPercent(change);

        setChangeClass(
            $("btcChange"),
            change
        );
    }


    /*
       ETH / SOL may not currently be supplied
       by the backend. Keep the interface ready
       without inventing market data.
    */

    if (data.eth_price !== undefined) {

        $("ethPrice").textContent =
            formatCurrency(data.eth_price);

    }

    if (data.eth_change_24h !== undefined) {

        const ethChange =
            Number(data.eth_change_24h);

        $("ethChange").textContent =
            formatPercent(ethChange);

        setChangeClass(
            $("ethChange"),
            ethChange
        );
    }


    if (data.sol_price !== undefined) {

        $("solPrice").textContent =
            formatCurrency(data.sol_price);

    }

    if (data.sol_change_24h !== undefined) {

        const solChange =
            Number(data.sol_change_24h);

        $("solChange").textContent =
            formatPercent(solChange);

        setChangeClass(
            $("solChange"),
            solChange
        );
    }


    if ($("marketUpdated")) {

        $("marketUpdated").textContent =
            `Updated ${formatTime()}`;
    }
}


function setChangeClass(element, value) {

    if (!element) {
        return;
    }

    element.classList.remove(
        "positive",
        "negative",
        "neutral"
    );

    if (Number(value) > 0) {

        element.classList.add("positive");

    } else if (Number(value) < 0) {

        element.classList.add("negative");

    } else {

        element.classList.add("neutral");
    }
}


/* =========================================================
   AI SIGNAL
========================================================= */

async function fetchSignal() {

    try {

        const data =
            await apiRequest("/api/signal");

        currentSignal = data;

        updateSignalUI(data);

    } catch (error) {

        console.error(
            "Signal request failed:",
            error
        );

        if ($("signalDescription")) {

            $("signalDescription").textContent =
                `Signal unavailable: ${error.message}`;
        }
    }
}


function updateSignalUI(data) {

    const action =
        String(
            data.action ||
            data.signal ||
            data.decision ||
            "HOLD"
        ).toUpperCase();


    const description =
        data.description ||
        data.reason ||
        data.message ||
        "Market analysis completed.";


    const confidence =
        Number(
            data.confidence ||
            data.confidence_score ||
            0
        );


    const trend =
        data.trend ||
        data.market_trend ||
        "—";


    const rsi =
        data.rsi;


    const price =
        data.price ||
        data.current_price;


    if ($("signalAction")) {

        $("signalAction").textContent =
            action;
    }


    if ($("signalBadge")) {

        $("signalBadge").textContent =
            action;

        $("signalBadge").classList.remove(
            "buy",
            "sell",
            "hold"
        );

        if (action === "BUY") {

            $("signalBadge")
                .classList.add("buy");

        } else if (action === "SELL") {

            $("signalBadge")
                .classList.add("sell");

        } else {

            $("signalBadge")
                .classList.add("hold");
        }
    }


    if ($("signalDescription")) {

        $("signalDescription").textContent =
            description;
    }


    if ($("confidenceValue")) {

        $("confidenceValue").textContent =
            `${confidence.toFixed(1)}%`;
    }


    if ($("confidenceBar")) {

        const safeConfidence =
            Math.max(
                0,
                Math.min(
                    100,
                    confidence
                )
            );

        $("confidenceBar").style.width =
            `${safeConfidence}%`;
    }


    if ($("marketTrend")) {

        $("marketTrend").textContent =
            String(trend).toUpperCase();
    }


    if ($("rsiValue")) {

        $("rsiValue").textContent =
            Number.isFinite(Number(rsi))
                ? Number(rsi).toFixed(2)
                : "—";
    }


    if ($("signalPrice")) {

        $("signalPrice").textContent =
            Number.isFinite(Number(price))
                ? formatCurrency(price)
                : "—";
    }


    updateIntelligence(
        action,
        trend,
        rsi
    );
}


function updateIntelligence(
    action,
    trend,
    rsi
) {

    if ($("priceAction")) {

        $("priceAction").textContent =
            action === "BUY"
                ? "Bullish"
                : action === "SELL"
                    ? "Bearish"
                    : "Neutral";
    }


    if ($("momentumStatus")) {

        const rsiNumber =
            Number(rsi);

        if (!Number.isFinite(rsiNumber)) {

            $("momentumStatus")
                .textContent = "Monitoring";

        } else if (rsiNumber >= 70) {

            $("momentumStatus")
                .textContent = "Overbought";

        } else if (rsiNumber <= 30) {

            $("momentumStatus")
                .textContent = "Oversold";

        } else {

            $("momentumStatus")
                .textContent = "Balanced";
        }
    }


    if ($("trendStatus")) {

        $("trendStatus").textContent =
            String(trend || "Monitoring")
                .toUpperCase();
    }
}


/* =========================================================
   PAPER ACCOUNT
========================================================= */

async function fetchPaperAccount() {

    try {

        const data =
            await apiRequest(
                "/api/paper-account"
            );

        updatePortfolioUI(data);

    } catch (error) {

        console.error(
            "Paper account request failed:",
            error
        );
    }
}


function updatePortfolioUI(data) {

    const portfolioValue =
        Number(
            data.portfolio_value ||
            data.total_equity ||
            0
        );


    const cash =
        Number(
            data.cash ||
            data.available_cash ||
            0
        );


    const btc =
        Number(
            data.btc ||
            data.btc_holdings ||
            0
        );


    const pnl =
        Number(
            data.profit_loss ||
            data.pnl ||
            0
        );


    if ($("portfolioValue")) {

        $("portfolioValue").textContent =
            formatCurrency(
                portfolioValue
            );
    }


    if ($("cashValue")) {

        $("cashValue").textContent =
            formatCurrency(cash);
    }


    if ($("btcHolding")) {

        $("btcHolding").textContent =
            `${formatNumber(btc, 8)} BTC`;
    }


    if ($("totalPnl")) {

        $("totalPnl").textContent =
            formatCurrency(pnl);

        $("totalPnl").style.color =
            pnl > 0
                ? "#46e49b"
                : pnl < 0
                    ? "#ff637b"
                    : "";
    }
}


/* =========================================================
   MANUAL BUY
========================================================= */

async function paperBuy() {

    setTradeMessage(
        "tradeMessage",
        "Submitting simulated BUY...",
        ""
    );


    const button =
        $("buyButton");

    if (button) {
        button.disabled = true;
    }


    try {

        const data =
            await apiRequest(
                "/api/paper-buy",
                {
                    method: "POST"
                }
            );


        setTradeMessage(
            "tradeMessage",
            data.message ||
            "Paper BUY executed successfully.",
            "success"
        );


        await fetchPaperAccount();
        await fetchSignal();
        await fetchAutoTradingStatus();

    } catch (error) {

        setTradeMessage(
            "tradeMessage",
            `BUY failed: ${error.message}`,
            "error"
        );

    } finally {

        if (button) {
            button.disabled = false;
        }
    }
}


/* =========================================================
   MANUAL SELL
========================================================= */

async function paperSell() {

    setTradeMessage(
        "tradeMessage",
        "Submitting simulated SELL...",
        ""
    );


    const button =
        $("sellButton");

    if (button) {
        button.disabled = true;
    }


    try {

        const data =
            await apiRequest(
                "/api/paper-sell",
                {
                    method: "POST"
                }
            );


        setTradeMessage(
            "tradeMessage",
            data.message ||
            "Paper SELL executed successfully.",
            "success"
        );


        await fetchPaperAccount();
        await fetchSignal();
        await fetchAutoTradingStatus();

    } catch (error) {

        setTradeMessage(
            "tradeMessage",
            `SELL failed: ${error.message}`,
            "error"
        );

    } finally {

        if (button) {
            button.disabled = false;
        }
    }
}


/* =========================================================
   AUTO TRADING STATUS
========================================================= */

async function fetchAutoTradingStatus() {

    try {

        const data =
            await apiRequest(
                "/api/auto-trading"
            );

        autoTradingEnabled =
            Boolean(data.enabled);

        updateAutoTradingUI(data);

    } catch (error) {

        console.error(
            "Auto-trading status failed:",
            error
        );

        setTradeMessage(
            "autoMessage",
            `Auto-trading status unavailable: ${error.message}`,
            "error"
        );
    }
}


function updateAutoTradingUI(data) {

    const enabled =
        Boolean(data.enabled);

    autoTradingEnabled =
        enabled;


    const badge =
        $("autoStatusBadge");

    const button =
        $("autoToggleButton");


    if (badge) {

        badge.textContent =
            enabled
                ? "ON"
                : "OFF";

        badge.classList.remove(
            "on",
            "off"
        );

        badge.classList.add(
            enabled
                ? "on"
                : "off"
        );
    }


    if (button) {

        button.textContent =
            enabled
                ? "DISABLE AUTO-TRADING"
                : "ENABLE AUTO-TRADING";

        button.classList.toggle(
            "enabled",
            enabled
        );
    }


    if ($("riskPosition") &&
        data.risk_settings) {

        const value =
            data.risk_settings
                .max_position_percent;

        if (value !== undefined) {

            $("riskPosition")
                .textContent =
                `${value}%`;
        }
    }


    if ($("riskConfidence") &&
        data.risk_settings) {

        const value =
            data.risk_settings
                .minimum_confidence;

        if (value !== undefined) {

            $("riskConfidence")
                .textContent =
                `${value}%`;
        }
    }


    if ($("riskCooldown") &&
        data.risk_settings) {

        const value =
            data.risk_settings
                .trade_cooldown_seconds;

        if (value !== undefined) {

            $("riskCooldown")
                .textContent =
                `${value}s`;
        }
    }


    updateTradeStats(data);
}


/* =========================================================
   AUTO TRADING TOGGLE
========================================================= */

async function toggleAutoTrading() {

    const button =
        $("autoToggleButton");


    if (button) {
        button.disabled = true;
    }


    setTradeMessage(
        "autoMessage",
        "Changing AI trading status...",
        ""
    );


    try {

        const data =
            await apiRequest(
                "/api/auto-trading/toggle",
                {
                    method: "POST"
                }
            );


        autoTradingEnabled =
            Boolean(data.enabled);


        await fetchAutoTradingStatus();


        setTradeMessage(
            "autoMessage",
            data.message ||
            (
                autoTradingEnabled
                    ? "AI auto-trading enabled."
                    : "AI auto-trading disabled."
            ),
            "success"
        );


    } catch (error) {

        /*
           This deliberately exposes the actual
           backend/browser error instead of only
           saying "Unable to change AI trading status."
        */

        setTradeMessage(
            "autoMessage",
            `Auto-trading error: ${error.message}`,
            "error"
        );

        console.error(
            "AUTO TOGGLE ERROR:",
            error
        );

    } finally {

        if (button) {
            button.disabled = false;
        }
    }
}


/* =========================================================
   RUN AUTO TRADING
========================================================= */

async function runAutoTrading() {

    try {

        const data =
            await apiRequest(
                "/api/auto-trading/run",
                {
                    method: "POST"
                }
            );


        if (data.message) {

            setTradeMessage(
                "autoMessage",
                data.message,
                "success"
            );
        }


        await fetchPaperAccount();
        await fetchAutoTradingStatus();

    } catch (error) {

        setTradeMessage(
            "autoMessage",
            `Auto-trading run failed: ${error.message}`,
            "error"
        );

        console.error(
            "Auto trading run failed:",
            error
        );
    }
}


/* =========================================================
   TRADE STATS
========================================================= */

function updateTradeStats(data) {

    const trades =
        Number(
            data.trades ||
            0
        );


    if ($("tradeCount")) {

        $("tradeCount").textContent =
            `${trades} ${trades === 1 ? "trade" : "trades"}`;
    }
}


/* =========================================================
   TRADE HISTORY
========================================================= */

async function fetchTradeHistory() {

    /*
       The current backend may not expose a
       /api/trades endpoint yet.

       We intentionally do not generate fake
       trade records.
    */

    try {

        const data =
            await apiRequest(
                "/api/trades"
            );

        renderTradeHistory(data);

    } catch (error) {

        /*
           Keep the dashboard usable when the
           endpoint has not been added yet.
        */

        console.info(
            "Trade history endpoint not available yet."
        );
    }
}


function renderTradeHistory(data) {

    const table =
        $("tradeHistory");

    if (!table) {
        return;
    }


    const trades =
        Array.isArray(data)
            ? data
            : Array.isArray(data.trades)
                ? data.trades
                : [];


    if (trades.length === 0) {

        table.innerHTML = `
            <tr>
                <td colspan="5">
                    No trades recorded yet.
                </td>
            </tr>
        `;

        return;
    }


    table.innerHTML =
        trades
            .slice()
            .reverse()
            .map(trade => {

                const action =
                    String(
                        trade.action ||
                        trade.side ||
                        "UNKNOWN"
                    ).toUpperCase();


                const price =
                    Number(
                        trade.price ||
                        trade.entry_price ||
                        0
                    );


                const quantity =
                    Number(
                        trade.quantity ||
                        trade.qty ||
                        0
                    );


                const status =
                    trade.status ||
                    "OPEN";


                return `
                    <tr>

                        <td class="${
                            action === "BUY"
                                ? "trade-buy"
                                : "trade-sell"
                        }">
                            ${escapeHtml(action)}
                        </td>

                        <td>
                            BTC / USD
                        </td>

                        <td>
                            ${formatCurrency(price)}
                        </td>

                        <td>
                            ${formatNumber(quantity, 8)}
                        </td>

                        <td>
                            ${escapeHtml(
                                String(status).toUpperCase()
                            )}
                        </td>

                    </tr>
                `;

            })
            .join("");
}


/* =========================================================
   BACKTEST
========================================================= */

async function runBacktest() {

    const button =
        $("backtestButton");


    const asset =
        $("assetSelect")
            ? $("assetSelect").value
            : "BTCUSDT";


    const strategy =
        $("strategySelect")
            ? $("strategySelect").value
            : "rsi_ma";


    const capital =
        $("initialCapital")
            ? Number(
                $("initialCapital").value
            )
            : 10000;


    if (button) {

        button.disabled = true;
        button.textContent =
            "RUNNING...";
    }


    const result =
        $("backtestResult");


    if (result) {

        result.innerHTML = `
            <span class="card-label">
                BACKTESTING
            </span>

            <p>
                Running ${escapeHtml(strategy)}
                on ${escapeHtml(asset)}...
            </p>
        `;
    }


    try {

        const data =
            await apiRequest(
                "/api/backtest",
                {
                    method: "POST",

                    body: JSON.stringify({
                        asset: asset,
                        strategy: strategy,
                        initial_capital: capital
                    })
                }
            );


        renderBacktestResult(data);

    } catch (error) {

        if (result) {

            result.innerHTML = `
                <span class="card-label">
                    BACKTEST ERROR
                </span>

                <p>
                    ${escapeHtml(error.message)}
                </p>
            `;
        }

    } finally {

        if (button) {

            button.disabled = false;
            button.textContent =
                "RUN BACKTEST";
        }
    }
}


function renderBacktestResult(data) {

    const result =
        $("backtestResult");

    if (!result) {
        return;
    }


    const returnValue =
        data.return ??
        data.net_return ??
        data.total_return;


    const finalValue =
        data.final_value ??
        data.final_balance ??
        data.portfolio_value;


    const winRate =
        data.win_rate;


    const trades =
        data.trades ??
        data.total_trades;


    result.innerHTML = `

        <span class="card-label">
            BACKTEST COMPLETE
        </span>

        <div class="result-grid">

            <div class="result-item">

                <span>
                    RETURN
                </span>

                <strong>
                    ${
                        returnValue !== undefined
                            ? formatPercent(returnValue)
                            : "—"
                    }
                </strong>

            </div>


            <div class="result-item">

                <span>
                    FINAL VALUE
                </span>

                <strong>
                    ${
                        finalValue !== undefined
                            ? formatCurrency(finalValue)
                            : "—"
                    }
                </strong>

            </div>


            <div class="result-item">

                <span>
                    WIN RATE
                </span>

                <strong>
                    ${
                        winRate !== undefined
                            ? `${Number(winRate).toFixed(2)}%`
                            : "—"
                    }
                </strong>

            </div>


            <div class="result-item">

                <span>
                    TRADES
                </span>

                <strong>
                    ${
                        trades !== undefined
                            ? trades
                            : "—"
                    }
                </strong>

            </div>

        </div>
    `;
}


/* =========================================================
   MESSAGE HELPER
========================================================= */

function setTradeMessage(
    elementId,
    message,
    type
) {

    const element =
        $(elementId);

    if (!element) {
        return;
    }

    element.textContent =
        message;

    element.classList.remove(
        "success",
        "error"
    );

    if (type) {

        element.classList.add(
            type
        );
    }
}


/* =========================================================
   HTML ESCAPE
========================================================= */

function escapeHtml(value) {

    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


/* =========================================================
   EVENT LISTENERS
========================================================= */

function setupEvents() {

    const buyButton =
        $("buyButton");

    const sellButton =
        $("sellButton");

    const autoToggleButton =
        $("autoToggleButton");

    const backtestButton =
        $("backtestButton");


    if (buyButton) {

        buyButton.addEventListener(
            "click",
            paperBuy
        );
    }


    if (sellButton) {

        sellButton.addEventListener(
            "click",
            paperSell
        );
    }


    if (autoToggleButton) {

        autoToggleButton.addEventListener(
            "click",
            toggleAutoTrading
        );
    }


    if (backtestButton) {

        backtestButton.addEventListener(
            "click",
            runBacktest
        );
    }
}


/* =========================================================
   INITIAL DATA LOAD
========================================================= */

async function loadDashboard() {

    await Promise.allSettled([
        fetchMarket(),
        fetchSignal(),
        fetchPaperAccount(),
        fetchAutoTradingStatus(),
        fetchTradeHistory()
    ]);
}


/* =========================================================
   REFRESH LOOPS
========================================================= */

function startRefreshLoops() {

    /*
       Market:
       every 10 seconds
    */

    setInterval(() => {

        fetchMarket();

    }, 10000);


    /*
       AI signal:
       every 30 seconds
    */

    setInterval(() => {

        fetchSignal();

    }, 30000);


    /*
       Paper portfolio:
       every 10 seconds
    */

    setInterval(() => {

        fetchPaperAccount();

    }, 10000);


    /*
       Auto trading status:
       every 10 seconds
    */

    setInterval(() => {

        fetchAutoTradingStatus();

    }, 10000);


    /*
       Trade history:
       every 15 seconds
    */

    setInterval(() => {

        fetchTradeHistory();

    }, 15000);
}


/* =========================================================
   START UPUPWAY AI
========================================================= */

async function startUpUpwayAI() {

    console.log(
        "========================================"
    );

    console.log(
        "UPUPWAY AI FRONTEND STARTING"
    );

    console.log(
        "API:",
        API_BASE_URL
    );

    console.log(
        "MODE: PAPER TRADING"
    );

    console.log(
        "========================================"
    );


    setupMobileMenu();

    setupEvents();

    await loadDashboard();

    startRefreshLoops();
}


/* =========================================================
   START
========================================================= */

document.addEventListener(
    "DOMContentLoaded",
    startUpUpwayAI
);
