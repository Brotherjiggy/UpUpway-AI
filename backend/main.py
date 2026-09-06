import os, time, asyncio, logging
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

APP_NAME='Upupway AI'; VERSION='1.5.2'; BUILD_ID='2026-09-06-v1.5.2'; MODE='paper'
COINGECKO_BASE_URL='https://api.coingecko.com/api/v3'; COINGECKO_TIMEOUT=15
SUPABASE_URL=os.getenv('SUPABASE_URL','').rstrip('/'); SUPABASE_SERVICE_ROLE_KEY=os.getenv('SUPABASE_SERVICE_ROLE_KEY','')
MARKET_CACHE_TTL=120.0; PROVIDER_MIN_INTERVAL=120.0; STALE_MARKET_MAX_AGE=86400.0
COLLECTOR_INTERVAL_SECONDS=120; LOCAL_HISTORY_MAX_POINTS=500; LOCAL_HISTORY_REQUIRED_POINTS=30
RSI_PERIOD=14; RSI_OVERSOLD=30.0; RSI_OVERBOUGHT=70.0; RSI_EXTREME_OVERSOLD=20.0; RSI_EXTREME_OVERBOUGHT=80.0
SHORT_MA_PERIOD=5; LONG_MA_PERIOD=14; MIN_HISTORY_MOVEMENT_PERCENT=0.05
PAPER_ACCOUNT_KEY='demo'; BOT_STATE_KEY='main'
RISK_SETTINGS={'max_position_percent':10.0,'minimum_confidence':55.0,'trade_cooldown_seconds':60.0,'stop_loss_percent':3.0,'take_profit_percent':6.0,'daily_loss_limit_percent':5.0,'max_consecutive_losses':3}
logging.basicConfig(level=logging.INFO); logger=logging.getLogger('upupway-ai')
market_cache=None; market_cache_time=0.0; last_provider_request=0.0; last_known_market=None; last_known_market_time=0.0
btc_history=deque(maxlen=LOCAL_HISTORY_MAX_POINTS); collector_running=False; collector_task=None

def utc_now(): return datetime.now(timezone.utc).isoformat()
def parse_timestamp(v):
    try: return datetime.fromisoformat(str(v).replace('Z','+00:00')) if v else None
    except Exception: return None

def supabase_configured(): return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)
def supabase_headers():
    if not supabase_configured(): raise RuntimeError('Supabase environment variables are missing.')
    return {'apikey':SUPABASE_SERVICE_ROLE_KEY,'Authorization':f'Bearer {SUPABASE_SERVICE_ROLE_KEY}','Content-Type':'application/json'}
def supabase_request(method,table,params=None,payload=None):
    if not supabase_configured(): raise RuntimeError('Supabase is not configured.')
    h=supabase_headers()
    if method.upper() in ('POST','PATCH','DELETE'): h['Prefer']='return=representation'
    r=requests.request(method.upper(),f'{SUPABASE_URL}/rest/v1/{table}',headers=h,params=params,json=payload,timeout=15)
    if not r.ok: raise RuntimeError(f'Supabase {method.upper()} {table} failed: {r.status_code} {r.text}')
    return r.json() if r.text else []

def default_paper_account():
    return {'account_key':PAPER_ACCOUNT_KEY,'starting_balance':10000.0,'cash':10000.0,'btc':0.0,'entry_price':None,'last_action':'NONE','profit_loss':0.0,'realized_profit_loss':0.0,'unrealized_profit_loss':0.0,'portfolio_value':10000.0,'updated_at':utc_now()}
def load_paper_account():
    try:
        rows=supabase_request('GET','paper_accounts',{'account_key':f'eq.{PAPER_ACCOUNT_KEY}','limit':'1'})
        if rows:
            a=rows[0]
            for k in ('starting_balance','cash','btc','entry_price','profit_loss','realized_profit_loss','unrealized_profit_loss','portfolio_value'):
                if a.get(k) is not None: a[k]=float(a[k])
            return a
        a=default_paper_account(); supabase_request('POST','paper_accounts',payload=a); return a
    except Exception as e: logger.warning('Could not load paper account: %s',e); return default_paper_account()
def save_paper_account(a):
    try:
        a['updated_at']=utc_now(); payload={k:a.get(k) for k in ('starting_balance','cash','btc','entry_price','last_action','profit_loss','realized_profit_loss','unrealized_profit_loss','portfolio_value','updated_at')}
        supabase_request('PATCH','paper_accounts',{'account_key':f'eq.{PAPER_ACCOUNT_KEY}'},payload)
    except Exception as e: logger.warning('Could not save paper account: %s',e)
paper_account=load_paper_account()

def default_bot_state(): return {'state_key':BOT_STATE_KEY,'enabled':False,'last_signal':'NONE','last_action':'NONE','last_price':None,'last_trade_time':None,'trades':0,'wins':0,'losses':0,'consecutive_losses':0,'updated_at':utc_now()}
def load_bot_state():
    try:
        rows=supabase_request('GET','bot_state',{'state_key':f'eq.{BOT_STATE_KEY}','limit':'1'})
        if rows:
            s=rows[0]; s.setdefault('consecutive_losses',0); return s
        s=default_bot_state(); supabase_request('POST','bot_state',payload=s); return s
    except Exception as e: logger.warning('Could not load bot state: %s',e); return default_bot_state()
auto_trading=load_bot_state()
def save_bot_state():
    try:
        auto_trading['updated_at']=utc_now(); payload={k:auto_trading.get(k) for k in ('enabled','last_signal','last_action','last_price','last_trade_time','trades','wins','losses','updated_at')}
        supabase_request('PATCH','bot_state',{'state_key':f'eq.{BOT_STATE_KEY}'},payload)
    except Exception as e: logger.warning('Could not save bot state: %s',e)

def save_trade(side,price,quantity,amount,reason,profit_loss=0.0,source='manual'):
    try: supabase_request('POST','trades',payload={'account_key':PAPER_ACCOUNT_KEY,'timestamp':utc_now(),'side':side,'symbol':'BTCUSDT','price':float(price),'quantity':float(quantity),'amount':float(amount),'reason':reason,'profit_loss':float(profit_loss),'source':source,'paper_only':True})
    except Exception as e: logger.warning('Could not save trade: %s',e)
def get_trades(limit=100):
    try: return supabase_request('GET','trades',{'account_key':f'eq.{PAPER_ACCOUNT_KEY}','order':'timestamp.desc','limit':str(limit)})
    except Exception as e: logger.warning('Could not load trades: %s',e); return []

def save_price_snapshot(price):
    try: supabase_request('POST','price_snapshots',payload={'symbol':'BTCUSDT','price':float(price),'captured_at':utc_now()})
    except Exception as e: logger.warning('Could not save price snapshot: %s',e)
def load_price_history():
    try:
        rows=supabase_request('GET','price_snapshots',{'symbol':'eq.BTCUSDT','order':'captured_at.desc','limit':str(LOCAL_HISTORY_MAX_POINTS)})
        btc_history.clear(); btc_history.extend(float(r['price']) for r in reversed(rows) if r.get('price') is not None)
        logger.info('Loaded %s BTC snapshots from Supabase.',len(btc_history))
    except Exception as e: logger.warning('Could not load price history: %s',e)
def get_signals(limit=100):
    try: return supabase_request('GET','signals',{'symbol':'eq.BTCUSDT','order':'timestamp.desc','limit':str(limit)})
    except Exception as e: logger.warning('Could not load signals: %s',e); return []
def save_signal(s):
    p={'timestamp':s.get('generated_at',utc_now()),'symbol':'BTCUSDT','action':s.get('action','HOLD'),'confidence':float(s.get('confidence',0)),'trend':s.get('trend','NEUTRAL'),'rsi':s.get('rsi'),'price':float(s.get('price',0)),'short_ma':s.get('short_ma'),'long_ma':s.get('long_ma'),'momentum':s.get('momentum'),'price_vs_ma':s.get('price_vs_ma'),'data_quality':s.get('data_quality'),'score':s.get('score'),'description':s.get('description'),'history_points':s.get('history_points'),'source':s.get('source','Upupway AI v1.5.2'),'paper_only':True}
    try: supabase_request('POST','signals',payload=p)
    except Exception as e: logger.warning('Could not save signal: %s',e)

def market_from_supabase():
    try:
        rows=supabase_request('GET','price_snapshots',{'symbol':'eq.BTCUSDT','order':'captured_at.desc','limit':'1'})
        if not rows:return None
        return {'symbol':'BTCUSDT','price':float(rows[0]['price']),'change_24h':None,'volume_24h':None,'source':'Supabase last known price','eth_price':None,'eth_change_24h':None,'sol_price':None,'sol_change_24h':None,'updated_at':rows[0].get('captured_at',utc_now()),'stale':True}
    except Exception as e: logger.warning('Could not retrieve fallback price: %s',e); return None

def fetch_coingecko_market():
    global last_provider_request
    now=time.time()
    if last_provider_request and now-last_provider_request<PROVIDER_MIN_INTERVAL: raise RuntimeError('CoinGecko provider cooldown active.')
    last_provider_request=now
    params={'ids':'bitcoin,ethereum,solana','vs_currencies':'usd','include_24hr_change':'true','include_24hr_vol':'true'}
    r=requests.get(f'{COINGECKO_BASE_URL}/simple/price',params=params,timeout=COINGECKO_TIMEOUT)
    if r.status_code==429: raise RuntimeError('CoinGecko rate limit reached (429).')
    r.raise_for_status(); d=r.json(); btc=d.get('bitcoin',{}); eth=d.get('ethereum',{}); sol=d.get('solana',{})
    if not btc.get('usd'): raise RuntimeError('CoinGecko returned no BTC price.')
    return {'symbol':'BTCUSDT','price':float(btc['usd']),'change_24h':btc.get('usd_24h_change'),'volume_24h':btc.get('usd_24h_vol'),'source':'CoinGecko','eth_price':eth.get('usd'),'eth_change_24h':eth.get('usd_24h_change'),'sol_price':sol.get('usd'),'sol_change_24h':sol.get('usd_24h_change'),'updated_at':utc_now(),'stale':False}
def get_market_data():
    global market_cache,market_cache_time,last_known_market,last_known_market_time
    now=time.time()
    if market_cache is not None and now-market_cache_time<MARKET_CACHE_TTL:
        out=dict(market_cache); out['cached']=True; out['cache_age_seconds']=round(now-market_cache_time,2); return out
    try:
        m=fetch_coingecko_market(); market_cache=m; market_cache_time=now; last_known_market=m; last_known_market_time=now; return m
    except Exception as e:
        logger.warning('CoinGecko unavailable: %s',e)
        if last_known_market is not None and now-last_known_market_time<=STALE_MARKET_MAX_AGE:
            f=dict(last_known_market); f.update(source='Cached market data',stale=True,cached=True,cache_age_seconds=round(now-last_known_market_time,2)); return f
        p=market_from_supabase()
        if p:return p
        raise HTTPException(503,'Market data provider unavailable and no cached market price is available.')

def record_local_price(price):
    try: btc_history.append(float(price))
    except Exception as e: logger.warning('Could not record local BTC price: %s',e)

def calculate_rsi(prices,period=RSI_PERIOD):
    if len(prices)<period+1:return None
    changes=[prices[i]-prices[i-1] for i in range(1,len(prices))]
    gains=[max(c,0.0) for c in changes]; losses=[max(-c,0.0) for c in changes]
    avg_gain=sum(gains[:period])/period; avg_loss=sum(losses[:period])/period
    for i in range(period,len(changes)):
        avg_gain=((avg_gain*(period-1))+gains[i])/period; avg_loss=((avg_loss*(period-1))+losses[i])/period
    if avg_gain==0 and avg_loss==0:return 50.0
    if avg_loss==0:return 100.0
    return 100.0-(100.0/(1.0+(avg_gain/avg_loss)))
def calculate_sma(prices,period): return sum(prices[-period:])/period if len(prices)>=period else None
def assess_data_quality(prices):
    if len(prices)<LOCAL_HISTORY_REQUIRED_POINTS:return {'quality':'POOR','reason':'Insufficient price history.','movement_percent':0.0}
    w=prices[-LOCAL_HISTORY_REQUIRED_POINTS:]; hi=max(w); lo=min(w); latest=w[-1]
    if latest<=0:return {'quality':'POOR','reason':'Invalid BTC price.','movement_percent':0.0}
    move=(hi-lo)/latest*100; unique=len(set(round(p,8) for p in w))
    if unique<=2:return {'quality':'POOR','reason':'Price history contains almost no variation.','movement_percent':round(move,4)}
    if move<MIN_HISTORY_MOVEMENT_PERCENT:return {'quality':'LIMITED','reason':'Market movement is very small.','movement_percent':round(move,4)}
    return {'quality':'GOOD','reason':'Sufficient history and meaningful price variation.','movement_percent':round(move,4)}
def calculate_momentum(prices):
    if len(prices)<10:return {'label':'UNKNOWN','percent':0.0}
    p,prev=prices[-1],prices[-10]
    if prev<=0:return {'label':'UNKNOWN','percent':0.0}
    pct=(p-prev)/prev*100
    label='STRONG_POSITIVE' if pct>=0.5 else 'POSITIVE' if pct>=0.15 else 'STRONG_NEGATIVE' if pct<=-0.5 else 'NEGATIVE' if pct<=-0.15 else 'WEAK'
    return {'label':label,'percent':round(pct,4)}

def analyze_signal(prices):
    price=prices[-1]; quality=assess_data_quality(prices)
    base={'rsi':None,'short_ma':None,'long_ma':None,'momentum':'UNKNOWN','momentum_percent':0.0,'price_vs_ma':'UNKNOWN','trend':'WARMING_UP','score':50.0,'bullish_confirmations':0,'bearish_confirmations':0,'conflict':False,'signal_strength':'NONE'}
    if len(prices)<LOCAL_HISTORY_REQUIRED_POINTS or quality['quality']=='POOR': return base|{'action':'HOLD','confidence':5.0 if quality['quality']=='POOR' else 0.0,'description':quality['reason'],'data_quality':quality['quality'],'data_movement_percent':quality['movement_percent']}
    rsi=calculate_rsi(prices); sma=calculate_sma(prices,SHORT_MA_PERIOD); lma=calculate_sma(prices,LONG_MA_PERIOD); mom=calculate_momentum(prices)
    if None in (rsi,sma,lma): return base|{'action':'HOLD','confidence':10.0,'description':'Technical indicators are not yet sufficiently established.','data_quality':quality['quality'],'data_movement_percent':quality['movement_percent'],'rsi':rsi,'short_ma':sma,'long_ma':lma,'momentum':mom['label'],'momentum_percent':mom['percent']}
    diff=(price-lma)/lma*100; pma='ABOVE' if diff>0.15 else 'BELOW' if diff<-0.15 else 'NEUTRAL'
    trend='BULLISH' if sma>lma and price>lma else 'BEARISH' if sma<lma and price<lma else 'NEUTRAL'
    score=50.0; reasons=[]; bull=bear=0
    if rsi<=RSI_EXTREME_OVERSOLD: score+=12; bull+=1; reasons.append('RSI is deeply oversold; possible bullish reversal.')
    elif rsi<RSI_OVERSOLD: score+=8; bull+=1; reasons.append('RSI is oversold; bullish reversal evidence.')
    elif rsi>=RSI_EXTREME_OVERBOUGHT: score-=12; bear+=1; reasons.append('RSI is deeply overbought; possible bearish reversal.')
    elif rsi>RSI_OVERBOUGHT: score-=8; bear+=1; reasons.append('RSI is overbought; bearish reversal evidence.')
    elif rsi<40: score+=4; reasons.append('RSI is moderately weak.')
    elif rsi>60: score-=4; reasons.append('RSI is moderately elevated.')
    else: reasons.append('RSI is neutral.')
    if sma>lma: score+=20; bull+=1; reasons.append('Short MA is above long MA.')
    elif sma<lma: score-=20; bear+=1; reasons.append('Short MA is below long MA.')
    if pma=='ABOVE': score+=10; bull+=1; reasons.append('Price is above the long MA.')
    elif pma=='BELOW': score-=10; bear+=1; reasons.append('Price is below the long MA.')
    else: reasons.append('Price is near the long MA.')
    mp=mom['percent']
    if mp>=0.5: score+=10; bull+=1; reasons.append('Momentum is strongly positive.')
    elif mp>=0.15: score+=5; bull+=1; reasons.append('Momentum is positive.')
    elif mp<=-0.5: score-=10; bear+=1; reasons.append('Momentum is strongly negative.')
    elif mp<=-0.15: score-=5; bear+=1; reasons.append('Momentum is negative.')
    else: reasons.append('Momentum is weak.')
    conflict=(rsi<30 and bear>=2 and mp<0) or (rsi>70 and bull>=2 and mp>0)
    if conflict:
        reasons.append('RSI conflicts with the dominant trend/momentum, so reversal risk is acknowledged without overriding the trend.')
    if score>=70 and bull>=2 and not conflict: action='BUY'
    elif score<=30 and bear>=2 and not conflict: action='SELL'
    else: action='HOLD'
    agreement=max(bull,bear); conf=45+abs(score-50)*1.1+(5 if agreement>=3 else -5 if agreement<=1 else 0)
    if quality['quality']=='LIMITED':conf-=10
    if conflict:conf=min(conf,55)
    conf=max(0,min(95,conf)); conf=min(conf,69) if action=='HOLD' else conf
    strength='STRONG' if agreement>=3 and abs(score-50)>=20 and not conflict else 'MODERATE' if agreement>=2 else 'WEAK'
    return {'action':action,'description':' | '.join(reasons),'confidence':round(conf,2),'trend':trend,'rsi':round(rsi,2),'price':float(price),'short_ma':round(sma,2),'long_ma':round(lma,2),'momentum':mom['label'],'momentum_percent':mom['percent'],'price_vs_ma':pma,'data_quality':quality['quality'],'data_movement_percent':quality['movement_percent'],'score':round(max(0,min(100,score)),2),'bullish_confirmations':bull,'bearish_confirmations':bear,'conflict':conflict,'signal_strength':strength}
def generate_signal():
    if not btc_history:
        m=get_market_data(); record_local_price(m['price'])
    a=analyze_signal(list(btc_history)); a.update({'source':'Upupway AI v1.5.2','paper_only':True,'history_points':len(btc_history),'generated_at':utc_now()}); save_signal(a); return a

def update_portfolio_value(price):
    btc_value=paper_account['btc']*price; paper_account['portfolio_value']=paper_account['cash']+btc_value
    paper_account['unrealized_profit_loss']=((price-paper_account['entry_price'])*paper_account['btc']) if paper_account['btc']>0 and paper_account.get('entry_price') else 0.0
    paper_account['profit_loss']=paper_account['portfolio_value']-paper_account['starting_balance']; save_paper_account(paper_account)
def execute_paper_buy(source='manual',reason='Manual paper buy'):
    price=float(get_market_data()['price']); allocation=paper_account['cash']*RISK_SETTINGS['max_position_percent']/100
    if allocation<=0: raise HTTPException(400,'Insufficient paper cash.')
    qty=allocation/price; old=paper_account['btc']
    if old and paper_account.get('entry_price'): paper_account['entry_price']=((old*paper_account['entry_price'])+allocation)/(old+qty)
    else: paper_account['entry_price']=price
    paper_account['cash']-=allocation; paper_account['btc']+=qty; paper_account['last_action']='BUY'; update_portfolio_value(price)
    save_trade('BUY',price,qty,allocation,reason,0,source); auto_trading.update(last_action='BUY',last_price=price); auto_trading['trades']+=1 if source=='auto' else 0; save_bot_state()
    return {'success':True,'action':'BUY','price':price,'quantity':qty,'amount':allocation,'source':source,'paper_only':True,'account':paper_account}
def execute_paper_sell(source='manual',reason='Manual paper sell'):
    price=float(get_market_data()['price']); qty=paper_account['btc']
    if qty<=0: raise HTTPException(400,'No BTC position to sell.')
    amount=qty*price; entry=paper_account.get('entry_price') or price; pnl=(price-entry)*qty
    paper_account['cash']+=amount; paper_account['btc']=0.0; paper_account['entry_price']=None; paper_account['last_action']='SELL'; paper_account['realized_profit_loss']+=pnl; update_portfolio_value(price)
    save_trade('SELL',price,qty,amount,reason,pnl,source); auto_trading.update(last_action='SELL',last_price=price); auto_trading['trades']+=1 if source=='auto' else 0
    if pnl>0:auto_trading['wins']+=1; auto_trading['consecutive_losses']=0
    elif pnl<0:auto_trading['losses']+=1; auto_trading['consecutive_losses']=auto_trading.get('consecutive_losses',0)+1
    save_bot_state(); return {'success':True,'action':'SELL','price':price,'quantity':qty,'amount':amount,'profit_loss':pnl,'source':source,'paper_only':True,'account':paper_account}
def cooldown_active():
    ts=parse_timestamp(auto_trading.get('last_trade_time')); return bool(ts and (datetime.now(timezone.utc)-ts).total_seconds()<RISK_SETTINGS['trade_cooldown_seconds'])
def risk_check():
    start=float(paper_account['starting_balance']); value=float(paper_account['portfolio_value'])
    if start<=0:return {'allowed':False,'reason':'Invalid starting balance.'}
    draw=(start-value)/start*100; cons=auto_trading.get('consecutive_losses',0)
    if draw>=RISK_SETTINGS['daily_loss_limit_percent']:return {'allowed':False,'reason':'Loss protection limit reached. Current protection is based on account drawdown from starting balance.','drawdown_percent':round(draw,2)}
    if cons>=RISK_SETTINGS['max_consecutive_losses']:return {'allowed':False,'reason':'Consecutive loss protection active.','consecutive_losses':cons}
    if cooldown_active():return {'allowed':False,'reason':'Trading cooldown active.'}
    return {'allowed':True,'reason':'Risk checks passed.','drawdown_percent':round(draw,2),'consecutive_losses':cons}
def run_auto_trading():
    if not auto_trading['enabled']:return {'success':False,'message':'Auto trading is disabled.','paper_only':True}
    m=get_market_data(); price=float(m['price']); record_local_price(price); update_portfolio_value(price)
    if paper_account['btc']>0 and paper_account.get('entry_price'):
        change=(price-paper_account['entry_price'])/paper_account['entry_price']*100
        if change<=-RISK_SETTINGS['stop_loss_percent']: r=execute_paper_sell('auto','Stop loss triggered'); auto_trading['last_trade_time']=utc_now(); save_bot_state(); return r
        if change>=RISK_SETTINGS['take_profit_percent']: r=execute_paper_sell('auto','Take profit triggered'); auto_trading['last_trade_time']=utc_now(); save_bot_state(); return r
    risk=risk_check()
    if not risk['allowed']:return {'success':False,'message':risk['reason'],'risk_blocked':True,'risk':risk,'paper_only':True}
    s=generate_signal(); auto_trading['last_signal']=s['action']; auto_trading['last_price']=price
    if s.get('data_quality')!='GOOD' or float(s.get('confidence',0))<RISK_SETTINGS['minimum_confidence']:save_bot_state(); return {'success':True,'action':'HOLD','reason':'Signal blocked by data quality or confidence threshold.','signal':s,'paper_only':True}
    if s['action']=='BUY' and paper_account['btc']<=0:r=execute_paper_buy('auto','AI BUY signal'); auto_trading['last_trade_time']=utc_now(); save_bot_state(); return r
    if s['action']=='SELL' and paper_account['btc']>0:r=execute_paper_sell('auto','AI SELL signal'); auto_trading['last_trade_time']=utc_now(); save_bot_state(); return r
    save_bot_state(); return {'success':True,'action':'HOLD','reason':'No trade conditions met.','signal':s,'paper_only':True}

def reset_everything():
    global paper_account,auto_trading
    paper_account=default_paper_account(); auto_trading=default_bot_state(); ok=True
    try:
        supabase_request('PATCH','paper_accounts',{'account_key':f'eq.{PAPER_ACCOUNT_KEY}'},paper_account); supabase_request('PATCH','bot_state',{'state_key':f'eq.{BOT_STATE_KEY}'},auto_trading)
        supabase_request('DELETE','trades',{'account_key':f'eq.{PAPER_ACCOUNT_KEY}'}); supabase_request('DELETE','signals',{'symbol':'eq.BTCUSDT'}); supabase_request('DELETE','price_snapshots',{'symbol':'eq.BTCUSDT'})
    except Exception as e: ok=False; logger.warning('Reset persistence error: %s',e)
    btc_history.clear(); return ok

def historical_btc_prices(days=30):
    r=requests.get(f'{COINGECKO_BASE_URL}/coins/bitcoin/market_chart',params={'vs_currency':'usd','days':days},timeout=COINGECKO_TIMEOUT)
    if r.status_code==429:raise RuntimeError('CoinGecko rate limit reached during backtest.')
    r.raise_for_status(); return [float(x[1]) for x in r.json().get('prices',[])]
def calculate_backtest_signal(prices):
    a=analyze_signal(prices); return a['action'],a['score'],a['confidence']
def run_backtest(days=30):
    try: prices=historical_btc_prices(days)
    except Exception as e: raise HTTPException(503,f'Backtest data unavailable: {e}')
    if len(prices)<LOCAL_HISTORY_REQUIRED_POINTS+1:raise HTTPException(400,'Not enough historical data.')
    start=10000.0; cash=start; btc=0.0; buys=sells=0
    for i in range(LOCAL_HISTORY_REQUIRED_POINTS,len(prices)):
        action,score,conf=calculate_backtest_signal(prices[:i+1]); price=prices[i]
        if action=='BUY' and conf>=RISK_SETTINGS['minimum_confidence'] and btc<=0:
            alloc=cash*0.10; btc+=alloc/price; cash-=alloc; buys+=1
        elif action=='SELL' and conf>=RISK_SETTINGS['minimum_confidence'] and btc>0:
            cash+=btc*price; btc=0; sells+=1
    final=cash+btc*prices[-1]; strategy=(final-start)/start*100; bh=((start/prices[0]*prices[-1])-start)/start*100
    return {'success':True,'version':VERSION,'days':days,'starting_balance':start,'ending_balance':round(final,2),'strategy_return_percent':round(strategy,2),'buy_and_hold_return_percent':round(bh,2),'difference_vs_buy_and_hold':round(strategy-bh,2),'data_points':len(prices),'buy_signals':buys,'sell_signals':sells,'paper_only':True,'signal_engine':'Shared v1.5.2 RSI + MA + Momentum + Data Quality'}

class AutoTradingRequest(BaseModel): enabled: bool
async def market_history_collector():
    logger.info('Market history collector started. Interval=%ss',COLLECTOR_INTERVAL_SECONDS)
    while collector_running:
        try:
            m=await asyncio.to_thread(get_market_data); p=m.get('price')
            if p: record_local_price(p); await asyncio.to_thread(save_price_snapshot,p); logger.info('BTC observation recorded: %.2f | history=%s | source=%s',p,len(btc_history),m.get('source'))
        except Exception as e: logger.warning('History collector warning: %s',e)
        await asyncio.sleep(COLLECTOR_INTERVAL_SECONDS)
@asynccontextmanager
async def lifespan(app):
    global collector_task,collector_running
    logger.info('Starting %s v%s',APP_NAME,VERSION); await asyncio.to_thread(load_price_history); collector_running=True; collector_task=asyncio.create_task(market_history_collector()); yield
    collector_running=False
    if collector_task:
        collector_task.cancel()
        try: await collector_task
        except asyncio.CancelledError: pass
app=FastAPI(title='UpUpway AI',version=VERSION,description='Intelligent AI crypto paper-trading backend.',lifespan=lifespan)
origins=[x.strip() for x in os.getenv('FRONTEND_ORIGINS','*').split(',') if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=False,allow_methods=['*'],allow_headers=['*'])
@app.get('/')
def root(): return {'name':APP_NAME,'status':'online','mode':MODE,'version':VERSION,'build_id':BUILD_ID,'message':'Upupway AI trading backend is running.','paper_trading':True,'real_money_trading':False,'supabase_persistence':supabase_configured()}
@app.get('/api/health')
def health(): return {'status':'healthy','service':APP_NAME,'version':VERSION,'mode':MODE,'paper_only':True,'supabase_configured':supabase_configured(),'history_points':len(btc_history),'collector_running':collector_running,'timestamp':utc_now()}
@app.get('/api/status')
def status(): return {'name':APP_NAME,'version':VERSION,'build_id':BUILD_ID,'status':'online','mode':MODE,'market_data':'CoinGecko + persistent fallback','signal_engine':'RSI + MA + Momentum + Data Quality v1.5.2','auto_trading':auto_trading['enabled'],'paper_trading':True,'real_money_trading':False,'api_keys_required':False,'supabase_persistence':supabase_configured(),'history_points':len(btc_history),'collector_running':collector_running,'collector_interval_seconds':COLLECTOR_INTERVAL_SECONDS}
@app.get('/api/market')
def market(): return get_market_data()
@app.get('/api/signal')
def signal(): get_market_data(); return generate_signal()
@app.get('/api/signals')
def signals(limit:int=100): return {'success':True,'count':len(get_signals(max(1,min(500,limit)))),'signals':get_signals(max(1,min(500,limit))),'paper_only':True}
@app.get('/api/history-status')
def history_status():
    q=assess_data_quality(list(btc_history)); return {'success':True,'history_points':len(btc_history),'required_points':LOCAL_HISTORY_REQUIRED_POINTS,'ready':len(btc_history)>=LOCAL_HISTORY_REQUIRED_POINTS,'max_points':LOCAL_HISTORY_MAX_POINTS,'data_quality':q['quality'],'movement_percent':q['movement_percent'],'quality_reason':q['reason'],'source':'Supabase persistent snapshots','collector_running':collector_running,'collector_interval_seconds':COLLECTOR_INTERVAL_SECONDS,'supabase_persistence':supabase_configured(),'paper_only':True}
@app.get('/api/paper-account')
def get_paper_account(): m=get_market_data(); update_portfolio_value(m['price']); return {**paper_account,'btc_price':m['price'],'paper_only':True}
@app.post('/api/paper-buy')
def paper_buy(): return execute_paper_buy()
@app.post('/api/paper-sell')
def paper_sell(): return execute_paper_sell()
@app.get('/api/trades')
def trades(): r=get_trades(); return {'success':True,'count':len(r),'trades':r,'paper_only':True}
@app.get('/api/risk-settings')
def risk_settings(): return {'success':True,'risk_settings':RISK_SETTINGS,'paper_only':True}
@app.get('/api/risk')
def risk(): return {'success':True,**risk_check(),'risk_settings':RISK_SETTINGS,'paper_only':True}
@app.get('/api/auto-trading')
def get_auto_trading(): return {'success':True,**auto_trading,'risk_settings':RISK_SETTINGS,'cooldown_active':cooldown_active(),'paper_only':True}
@app.post('/api/auto-trading/toggle')
def toggle_auto_trading(request:AutoTradingRequest):
    auto_trading['enabled']=request.enabled
    if not request.enabled:auto_trading['last_action']='OFF'
    save_bot_state(); return {'success':True,'enabled':auto_trading['enabled'],'paper_only':True,'message':'Auto trading enabled.' if request.enabled else 'Auto trading disabled.'}
@app.post('/api/auto-trading/run')
def auto_trading_run(): return run_auto_trading()
@app.post('/api/paper-account/reset')
def paper_account_reset(): return {'success':True,'persistence_reset':reset_everything(),'account':paper_account,'auto_trading':auto_trading,'paper_only':True}
@app.post('/api/backtest')
def backtest(days:int=30): return run_backtest(max(1,min(365,days)))
if __name__=='__main__':
    import uvicorn; uvicorn.run('main:app',host='0.0.0.0',port=int(os.getenv('PORT','8000')))
