/* =========================================================
   UPUPWAY AI
   PROFESSIONAL PAPER TRADING FRONTEND
========================================================= */


/* =========================================================
   CONFIGURATION
========================================================= */

const API_BASE_URL = "http://127.0.0.1:8000";


/* =========================================================
   DOM HELPERS
========================================================= */

const $ = (id) =>
    document.getElementById(id);


/* =========================================================
   ELEMENTS
========================================================= */

const menuButton =
    $("menuButton");

const mainNav =
    $("mainNav");

const connectionText =
    $("connectionText");

const aiStatus =
    $("aiStatus");

const btcPrice =
    $("btcPrice");

const btcChange =
    $("btcChange");

const btcVolume =
    $("btcVolume");

const dataSource =
    $("dataSource");

const ethPrice =
    $("ethPrice");

const ethChange =
    $("ethChange");

const solPrice =
    $("solPrice");

const solChange =
    $("solChange");

const signalAction =
    $("signalAction");

const signalDescription =
    $("signalDescription");

const confidenceValue =
    $("confidenceValue");

const confidenceBar =
    $("confidenceBar");

const marketTrend =
    $("marketTrend");

const rsiValue =
    $("rsiValue");

const autoTradingButton =
    $("autoTradingButton");

const autoStatusText =
    $("autoStatusText");

const autoStatusDot =
    $("autoStatusDot");

const autoStatusDescription =
    $("autoStatusDescription");

const autoMessage =
    $("autoMessage");

const paperBuyButton =
    $("paperBuyButton");

const paperSellButton =
    $("paperSellButton");

const tradeMessage =
    $("tradeMessage");

const portfolioValue =
    $("portfolioValue");

const dailyPnl =
    $("dailyPnl");

const totalReturn =
    $("totalReturn");

const winRate =
    $("winRate");

const cashBalance =
    $("cashBalance");

const positionAmount =
    $("positionAmount");

const entryPrice =
    $("entryPrice");

const positionPnl =
    $("positionPnl");

const tradeHistory =
    $("tradeHistory");

const backtestButton =
    $("backtestButton");

const backtestResult =
    $("backtestResult");

const assetSelect =
    $("assetSelect");

const strategySelect =
    $("strategySelect");

const initialCapital =
    $("initialCapital");


/* =========================================================
   FORMATTERS
========================================================= */

function formatCurrency(value) {

    if (
        value === null ||
        value === undefined ||
        Number.isNaN(Number(value))
    ) {

        return "--";

    }


    return new Intl.NumberFormat(
        "en-US",
        {
            style: "currency",
            currency: "USD",
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        }
    ).format(
        Number(value)
    );

}


function formatNumber(value) {

    if (
        value === null ||
        value === undefined ||
        Number.isNaN(Number(value))
    ) {

        return "--";

    }


    return new Intl.NumberFormat(
        "en-US",
        {
            maximumFractionDigits: 8
        }
    ).format(
        Number(value)
    );

}


function formatPercent(value) {

    if (
        value === null ||
        value === undefined ||
        Number.isNaN(Number(value))
    ) {

        return "--";

    }


    const number =
        Number(value);


    return `${number >= 0 ? "+" : ""}${number.toFixed(2)}%`;

}


/* =========================================================
   API REQUEST
========================================================= */

async function apiRequest(
    endpoint,
    options = {}
) {

    const response =
        await fetch(
            `${API_BASE_URL}${endpoint}`,
            {
                ...options,

                headers: {
                    "Content-Type":
                        "application/json",

                    ...(options.headers || {})
                }
            }
        );


    const text =
        await response.text();


    let data = {};


    try {

        data =
            text
                ? JSON.parse(text)
                : {};

    } catch {

        data = {
            raw: text
        };

    }


    if (!response.ok) {

        throw new Error(
            data.detail ||
            data.message ||
            `HTTP ${response.status}`
        );

    }


    return data;

}


/* =========================================================
   CONNECTION STATUS
========================================================= */

function setConnected() {

    if (connectionText) {

        connectionText.textContent =
            "Backend connected";

    }


    if (aiStatus) {

        aiStatus.textContent =
            "Online";

    }

}


function setDisconnected() {

    if (connectionText) {

        connectionText.textContent =
            "Backend offline";

    }


    if (aiStatus) {

        aiStatus.textContent =
            "Offline";

    }

}


/* =========================================================
   MOBILE MENU
========================================================= */

if (menuButton && mainNav) {

    menuButton.addEventListener(
        "click",
        () => {

            mainNav.classList.toggle(
                "open"
            );

        }
    );


    mainNav
        .querySelectorAll("a")
        .forEach(link => {

            link.addEventListener(
                "click",
                () => {

                    mainNav.classList.remove(
                        "open"
                    );

                }
            );

        });

}


/* =========================================================
   MARKET DATA
========================================================= */

async function fetchMarketData() {

    try {

        const data =
            await apiRequest(
                "/api/market"
            );


        console.log(
            "UPUPWAY MARKET:",
            data
        );


        /*
           BTC backend response:

           {
             symbol,
             price,
             change_24h,
             volume_24h,
             source
           }
        */


        const btc =
            data.bitcoin ||
            data.BTC ||
            data;


        const eth =
            data.ethereum ||
            data.ETH ||
            {};


        const sol =
            data.solana ||
            data.SOL ||
            {};


        if (btcPrice) {

            btcPrice.textContent =
                formatCurrency(
                    btc.price
                );

        }


        if (btcChange) {

            btcChange.textContent =
                formatPercent(
                    btc.change_24h
                );


            updateChangeClass(
                btcChange,
                btc.change_24h
            );

        }


        if (btcVolume) {

            btcVolume.textContent =
                btc.volume_24h !== null &&
                btc.volume_24h !== undefined
                    ? formatCurrency(
                        btc.volume_24h
                    )
                    : "--";

        }


        if (dataSource) {

            dataSource.textContent =
                btc.source ||
                data.source ||
                "Connected";

        }


        if (
            eth &&
            eth.price !== undefined
        ) {

            ethPrice.textContent =
                formatCurrency(
                    eth.price
                );

        }


        if (ethChange) {

            ethChange.textContent =
                formatPercent(
                    eth.change_24h
                );

        }


        if (
            sol &&
            sol.price !== undefined
        ) {

            solPrice.textContent =
                formatCurrency(
                    sol.price
                );

        }


        if (solChange) {

            solChange.textContent =
                formatPercent(
                    sol.change_24h
                );

        }


        setConnected();


    } catch (error) {

        console.error(
            "Market error:",
            error
        );


        setDisconnected();

    }

}


/* =========================================================
   CHANGE COLOR
========================================================= */

function updateChangeClass(
    element,
    value
) {

    if (!element) {
        return;
    }


    element.classList.remove(
        "negative",
        "neutral"
    );


    if (
        value === null ||
        value === undefined ||
        Number.isNaN(Number(value))
    ) {

        element.classList.add(
            "neutral"
        );

        return;

    }


    if (Number(value) < 0) {

        element.classList.add(
            "negative"
        );

    }

}


/* =========================================================
   AI SIGNAL
========================================================= */

async function fetchAISignal() {

    try {

        const data =
            await apiRequest(
                "/api/signal"
            );


        console.log(
            "UPUPWAY AI SIGNAL:",
            data
        );


        const action =
            String(
                data.action ||
                data.signal ||
                "HOLD"
            ).toUpperCase();


        if (signalAction) {

            signalAction.textContent =
                action;

            signalAction.style.color =
                action === "BUY"
                    ? "#31d18b"
                    : action === "SELL"
                        ? "#ff5964"
                        : "#987cff";

        }


        if (signalDescription) {

            signalDescription.textContent =
                data.description ||
                `Current AI decision: ${action}`;

        }


        const confidence =
            Number(
                data.confidence ?? 0
            );


        if (confidenceValue) {

            confidenceValue.textContent =
                `${Math.max(
                    0,
                    Math.min(
                        100,
                        confidence
                    )
                ).toFixed(0)}%`;

        }


        if (confidenceBar) {

            confidenceBar.style.width =
                `${Math.max(
                    0,
                    Math.min(
                        100,
                        confidence
                    )
                )}%`;

        }


        if (marketTrend) {

            marketTrend.textContent =
                data.trend ||
                data.market_trend ||
                "Analyzing";

        }


        if (rsiValue) {

            rsiValue.textContent =
                data.rsi !== undefined &&
                data.rsi !== null
                    ? Number(
                        data.rsi
                    ).toFixed(2)
                    : "--";

        }

    } catch (error) {

        console.error(
            "AI signal error:",
            error
        );

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


        console.log(
            "PAPER ACCOUNT:",
            data
        );


        const cash =
            data.cash ??
            data.available_cash ??
            0;


        const btc =
            data.btc ??
            0;


        const value =
            data.portfolio_value ??
            data.total_value ??
            0;


        const pnl =
            data.unrealized_profit_loss ??
            data.profit_loss ??
            data.pnl ??
            0;


        if (cashBalance) {

            cashBalance.textContent =
                formatCurrency(
                    cash
                );

        }


        if (portfolioValue) {

            portfolioValue.textContent =
                formatCurrency(
                    value
                );

        }


        if (dailyPnl) {

            dailyPnl.textContent =
                formatCurrency(
                    pnl
                );

        }


        if (positionAmount) {

            positionAmount.textContent =
                `${formatNumber(
                    btc
                )} BTC`;

        }


        if (entryPrice) {

            entryPrice.textContent =
                formatCurrency(
                    data.entry_price
                );

        }


        if (positionPnl) {

            positionPnl.textContent =
                formatCurrency(
                    pnl
                );

        }

    } catch (error) {

        console.error(
            "Paper account error:",
            error
        );

    }

}


/* =========================================================
   PERFORMANCE
========================================================= */

async function fetchPerformance() {

    try {

        const data =
            await apiRequest(
                "/api/performance"
            );


        console.log(
            "PERFORMANCE:",
            data
        );


        if (
            data.portfolio_value !==
            undefined &&
            portfolioValue
        ) {

            portfolioValue.textContent =
                formatCurrency(
                    data.portfolio_value
                );

        }


        if (
            data.total_profit_loss !==
            undefined &&
            dailyPnl
        ) {

            dailyPnl.textContent =
                formatCurrency(
                    data.total_profit_loss
                );

        }


        if (
            data.total_return_percent !==
            undefined &&
            totalReturn
        ) {

            totalReturn.textContent =
                formatPercent(
                    data.total_return_percent
                );

        }


        if (
            data.win_rate !==
            undefined &&
            winRate
        ) {

            winRate.textContent =
                `${Number(
                    data.win_rate
                ).toFixed(1)}%`;

        }

    } catch (error) {

        /*
           Performance may not exist in every
           backend version. This is intentionally
           non-fatal.
        */

        console.warn(
            "Performance endpoint:",
            error.message
        );

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


        console.log(
            "AUTO TRADING:",
            data
        );


        updateAutoTradingUI(
            Boolean(
                data.enabled
            )
        );


    } catch (error) {

        console.error(
            "Auto trading status:",
            error
        );

    }

}


/* =========================================================
   AUTO TRADING UI
========================================================= */

function updateAutoTradingUI(
    enabled
) {

    if (autoStatusText) {

        autoStatusText.textContent =
            enabled
                ? "ACTIVE"
                : "PAUSED";

    }


    if (autoStatusDescription) {

        autoStatusDescription.textContent =
            enabled
                ? "UpUpway AI is actively monitoring the market in paper-trading mode."
                : "AI automated paper execution is currently paused.";

    }


    if (autoTradingButton) {

        autoTradingButton.textContent =
            enabled
                ? "Pause AI"
                : "Activate AI";

    }


    if (autoStatusDot) {

        autoStatusDot.classList.toggle(
            "active",
            enabled
        );

    }

}


/* =========================================================
   TOGGLE AUTO TRADING
========================================================= */

async function toggleAutoTrading() {

    if (!autoTradingButton) {
        return;
    }


    autoTradingButton.disabled =
        true;


    autoTradingButton.textContent =
        "Connecting...";


    if (autoMessage) {

        autoMessage.textContent =
            "Connecting to UpUpway AI engine...";

    }


    try {

        console.log(
            "Checking auto-trading status..."
        );


        const statusResponse =
            await fetch(
                `${API_BASE_URL}/api/auto-trading`,
                {
                    method: "GET",
                    cache: "no-store"
                }
            );


        if (!statusResponse.ok) {

            throw new Error(
                `Status request failed: HTTP ${statusResponse.status}`
            );

        }


        const statusData =
            await statusResponse.json();


        console.log(
            "Current status:",
            statusData
        );


        console.log(
            "Calling /api/auto-trading/toggle..."
        );


        const toggleResponse =
            await fetch(
                `${API_BASE_URL}/api/auto-trading/toggle`,
                {
                    method: "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    cache: "no-store"
                }
            );


        const responseText =
            await toggleResponse.text();


        console.log(
            "Toggle response:",
            responseText
        );


        if (!toggleResponse.ok) {

            throw new Error(
                `Toggle failed: HTTP ${toggleResponse.status} — ${responseText}`
            );

        }


        let result;


        try {

            result =
                JSON.parse(
                    responseText
                );

        } catch {

            throw new Error(
                "Backend returned invalid JSON."
            );

        }


        console.log(
            "Toggle result:",
            result
        );


        updateAutoTradingUI(
            Boolean(
                result.enabled
            )
        );


        if (autoMessage) {

            autoMessage.textContent =
                result.enabled
                    ? "✅ UpUpway AI paper auto-trading is ACTIVE."
                    : "⏸ AI paper auto-trading is PAUSED.";

        }


    } catch (error) {

        console.error(
            "AUTO TRADING ERROR:",
            error
        );


        if (autoMessage) {

            autoMessage.textContent =
                `Connection error: ${error.message}`;

        }


        /*
           Restore the actual backend state
           rather than blindly assuming it is off.
        */

        try {

            await fetchAutoTradingStatus();

        } catch {

            autoTradingButton.textContent =
                "Activate AI";

        }

    }


    autoTradingButton.disabled =
        false;

}


if (autoTradingButton) {

    autoTradingButton.addEventListener(
        "click",
        toggleAutoTrading
    );

}


/* =========================================================
   PAPER BUY
========================================================= */

async function paperBuy() {

    if (!paperBuyButton) {
        return;
    }


    paperBuyButton.disabled =
        true;


    if (tradeMessage) {

        tradeMessage.textContent =
            "Executing simulated BUY...";

    }


    try {

        const result =
            await apiRequest(
                "/api/paper-buy",
                {
                    method: "POST"
                }
            );


        console.log(
            "PAPER BUY RESULT:",
            result
        );


        if (tradeMessage) {

            tradeMessage.textContent =
                result.message ||
                "✅ Paper BUY executed.";

        }


        await refreshTradingData();


    } catch (error) {

        console.error(
            "Paper BUY:",
            error
        );


        if (tradeMessage) {

            tradeMessage.textContent =
                `BUY unavailable: ${error.message}`;

        }

    }


    paperBuyButton.disabled =
        false;

}


if (paperBuyButton) {

    paperBuyButton.addEventListener(
        "click",
        paperBuy
    );

}


/* =========================================================
   PAPER SELL
========================================================= */

async function paperSell() {

    if (!paperSellButton) {
        return;
    }


    paperSellButton.disabled =
        true;


    if (tradeMessage) {

        tradeMessage.textContent =
            "Executing simulated SELL...";

    }


    try {

        const result =
            await apiRequest(
                "/api/paper-sell",
                {
                    method: "POST"
                }
            );


        console.log(
            "PAPER SELL RESULT:",
            result
        );


        if (tradeMessage) {

            tradeMessage.textContent =
                result.message ||
                "✅ Paper SELL executed.";

        }


        await refreshTradingData();


    } catch (error) {

        console.error(
            "Paper SELL:",
            error
        );


        if (tradeMessage) {

            tradeMessage.textContent =
                `SELL unavailable: ${error.message}`;

        }

    }


    paperSellButton.disabled =
        false;

}


/* =========================================================
   TRADE HISTORY
========================================================= */

async function fetchTradeHistory() {

    try {

        const data =
            await apiRequest(
                "/api/trades"
            );


        console.log(
            "TRADE HISTORY:",
            data
        );


        if (!tradeHistory) {
            return;
        }


        const trades =
            Array.isArray(data)
                ? data
                : (
                    data.trades ||
                    data.history ||
                    []
                );


        if (!trades.length) {

            tradeHistory.innerHTML = `

                <div class="history-empty">

                    <div class="history-icon">
                        ≡
                    </div>

                    <strong>
                        No trades yet
                    </strong>

                    <span>
                        Paper executions will appear here.
                    </span>

                </div>

            `;

            return;

        }


        tradeHistory.innerHTML =
            trades
                .slice()
                .reverse()
                .map(
                    trade => {

                        const action =
                            String(
                                trade.action ||
                                trade.side ||
                                trade.type ||
                                "TRADE"
                            ).toUpperCase();


                        const symbol =
                            trade.symbol ||
                            "BTCUSDT";


                        const price =
                            trade.price ??
                            trade.entry_price ??
                            0;


                        const quantity =
                            trade.quantity ??
                            trade.amount ??
                            trade.btc_amount ??
                            0;


                        return `

                            <div class="trade-row">

                                <strong>
                                    ${escapeHtml(
                                        action
                                    )}
                                </strong>

                                <span>
                                    ${escapeHtml(
                                        symbol
                                    )}
                                </span>

                                <span>
                                    ${formatCurrency(
                                        price
                                    )}
                                </span>

                                <span>
                                    ${formatNumber(
                                        quantity
                                    )}
                                </span>

                            </div>

                        `;

                    }
                )
                .join("");

    } catch (error) {

        /*
           Do not break the dashboard if
           trade history isn't available.
        */

        console.warn(
            "Trade history:",
            error.message
        );

    }

}


/* =========================================================
   ESCAPE HTML
========================================================= */

function escapeHtml(value) {

    return String(value)
        .replace(
            /&/g,
            "&amp;"
        )
        .replace(
            /</g,
            "&lt;"
        )
        .replace(
            />/g,
            "&gt;"
        )
        .replace(
            /"/g,
            "&quot;"
        )
        .replace(
            /'/g,
            "&#039;"
        );

}


/* =========================================================
   BACKTEST
========================================================= */

async function runBacktest() {

    if (!backtestButton) {
        return;
    }


    const asset =
        assetSelect
            ? assetSelect.value
            : "BTC";


    const strategy =
        strategySelect
            ? strategySelect.value
            : "AI";


    const capital =
        initialCapital
            ? Number(
                initialCapital.value
            )
            : 10000;


    if (
        !capital ||
        capital <= 0
    ) {

        if (backtestResult) {

            backtestResult.innerHTML = `

                <span>
                    BACKTEST RESULTS
                </span>

                <strong>
                    Enter a valid starting capital.
                </strong>

            `;

        }

        return;

    }


    backtestButton.disabled =
        true;


    backtestButton.textContent =
        "Running...";


    if (backtestResult) {

        backtestResult.innerHTML = `

            <span>
                BACKTEST RESULTS
            </span>

            <strong>
                Analyzing historical data...
            </strong>

        `;

    }


    try {

        const result =
            await apiRequest(
                "/api/backtest",
                {
                    method: "POST",

                    body: JSON.stringify({

                        symbol:
                            `${asset}USDT`,

                        strategy:
                            strategy,

                        initial_capital:
                            capital

                    })

                }
            );


        console.log(
            "BACKTEST RESULT:",
            result
        );


        if (backtestResult) {

            const resultText =
                result.message ||
                result.summary ||
                "Backtest completed successfully.";


            backtestResult.innerHTML = `

                <span>
                    BACKTEST RESULTS
                </span>

                <strong>
                    ${escapeHtml(
                        resultText
                    )}
                </strong>

            `;

        }

    } catch (error) {

        console.error(
            "Backtest:",
            error
        );


        if (backtestResult) {

            backtestResult.innerHTML = `

                <span>
                    BACKTEST RESULTS
                </span>

                <strong>
                    Backtest unavailable:
                    ${escapeHtml(
                        error.message
                    )}
                </strong>

            `;

        }

    }


    backtestButton.disabled =
        false;


    backtestButton.textContent =
        "Run Backtest";

}


if (backtestButton) {

    backtestButton.addEventListener(
        "click",
        runBacktest
    );

}


/* =========================================================
   REFRESH ALL TRADING DATA
========================================================= */

async function refreshTradingData() {

    await Promise.allSettled([

        fetchPaperAccount(),

        fetchPerformance(),

        fetchAutoTradingStatus(),

        fetchTradeHistory()

    ]);

}


/* =========================================================
   START APPLICATION
========================================================= */

async function startUpUpwayAI() {

    console.log(
        "================================="
    );

    console.log(
        "🚀 UPUPWAY AI STARTING"
    );

    console.log(
        "================================="
    );


    await Promise.allSettled([

        fetchMarketData(),

        fetchAISignal(),

        refreshTradingData()

    ]);


    console.log(
        "✅ UPUPWAY AI FRONTEND ONLINE"
    );


    /*
       Market refresh:
       every 10 seconds
    */

    setInterval(
        fetchMarketData,
        10000
    );


    /*
       AI signal refresh:
       every 30 seconds
    */

    setInterval(
        fetchAISignal,
        30000
    );


    /*
       Trading/account refresh:
       every 15 seconds
    */

    setInterval(
        refreshTradingData,
        15000
    );

}


/* =========================================================
   INITIALIZE
========================================================= */

if (
    document.readyState ===
    "loading"
) {

    document.addEventListener(
        "DOMContentLoaded",
        startUpUpwayAI
    );

} else {

    startUpUpwayAI();

}
