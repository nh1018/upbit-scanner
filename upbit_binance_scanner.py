import csv, json, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError

VERSION='1.3-cloud'
BASE=Path(__file__).resolve().parent; DATA_DIR=BASE/'data'; OUT_DIR=BASE/'output'
DATA_DIR.mkdir(exist_ok=True); OUT_DIR.mkdir(exist_ok=True)
KST=timezone(timedelta(hours=9)); UTC=timezone.utc
UA=f'Mozilla/5.0 UpbitBinanceScannerV{VERSION}/{VERSION}'; UPBIT='https://api.upbit.com'; BINANCE='https://data-api.binance.vision'
MIN_UPBIT_24H_KRW=100_000_000

# State thresholds: pre-pump scanner should reject coins whose price already moved materially.
STARTING_3D_PCT=15.0
STARTING_5D_PCT=20.0
ACTIVATED_24H_PCT=15.0
ACTIVATED_3D_PCT=25.0
ACTIVATED_5D_PCT=35.0

CAUTION_NAMES={
 'PRICE_FLUCTUATIONS':'가격급등락','TRADING_VOLUME_SOARING':'거래량급등',
 'DEPOSIT_AMOUNT_SOARING':'입금량급등','GLOBAL_PRICE_DIFFERENCES':'글로벌가격차이',
 'CONCENTRATION_OF_SMALL_ACCOUNTS':'소수계정집중'
}

def get_json(base,path,params=None,timeout=15,retries=3):
    url=base+path+('?' + urlencode(params,doseq=True) if params else '')
    last=None
    for i in range(retries):
        try:
            req=Request(url,headers={'Accept':'application/json','User-Agent':UA})
            with urlopen(req,timeout=timeout) as r:return json.loads(r.read().decode())
        except (HTTPError,URLError,TimeoutError) as e:
            last=e; time.sleep(.8*(i+1))
    raise RuntimeError(f'API request failed: {url}\n{last}')

def pct(a,b):return None if a is None or b in (None,0) else (a/b-1)*100
def ratio(a,b):return None if a is None or b in (None,0) else a/b
def avg(xs):
    xs=[x for x in xs if x is not None]
    return sum(xs)/len(xs) if xs else None

def upbit_markets():return [r for r in get_json(UPBIT,'/v1/market/all',{'is_details':'true'}) if r['market'].startswith('KRW-')]
def upbit_tickers(markets):
    out=[]; codes=[m['market'] for m in markets]
    for i in range(0,len(codes),80):
        out+=get_json(UPBIT,'/v1/ticker',{'markets':','.join(codes[i:i+80])});time.sleep(.1)
    return {r['market']:r for r in out}
def upbit_daily(market,count=10):return get_json(UPBIT,'/v1/candles/days',{'market':market,'count':count})

def binance_exchange_info():
    out={}
    for s in get_json(BINANCE,'/api/v3/exchangeInfo').get('symbols',[]):
        if s.get('quoteAsset')=='USDT' and s.get('status')=='TRADING' and s.get('isSpotTradingAllowed',True):out[s['baseAsset']]=s['symbol']
    return out
def binance_24h():return {r['symbol']:r for r in get_json(BINANCE,'/api/v3/ticker/24hr') if r['symbol'].endswith('USDT')}
def binance_daily(symbol,limit=10):return get_json(BINANCE,'/api/v3/klines',{'symbol':symbol,'interval':'1d','limit':limit})

def upbit_completed(candles,now_kst):
    today_utc=now_kst.astimezone(UTC).date();done=[]
    for c in candles:
        d=datetime.fromisoformat(c['candle_date_time_utc']).replace(tzinfo=UTC).date()
        if d<today_utc:done.append(c)
    return done[:6]
def binance_completed(klines,now_kst):
    now_ms=int(now_kst.timestamp()*1000);done=[r for r in klines if int(r[6])<now_ms]
    done.sort(key=lambda r:int(r[0]),reverse=True);return done[:6]

def latest_rise_streak(q):
    # q = D1,D2,... newest first. 3 means D3 < D2 < D1 (three-day continuous acceleration).
    if len(q)<2:return 0
    streak=1
    for i in range(len(q)-1):
        if q[i]>q[i+1]:streak+=1
        else:break
    return streak

def base_metrics(q,close):
    if len(q)<5:return {}
    return {
      'q':q,'q_vs_prev':ratio(q[0],q[1]),'q_vs_3avg':ratio(q[0],avg(q[1:4])),
      'q_vs_5avg':ratio(q[0],avg(q[1:6])) if len(q)>=6 else None,
      'rise_streak':latest_rise_streak(q[:5]),
      'd2_vs_prior3':ratio(q[1],avg(q[2:5])) if len(q)>=5 else None,
      'd1_vs_prior4':ratio(q[0],avg(q[1:5])) if len(q)>=5 else None,
      'p3':pct(close[0],close[3]) if len(close)>=4 else None,
      'p5':pct(close[0],close[5]) if len(close)>=6 else None
    }
def metrics_upbit(done):return base_metrics([float(x['candle_acc_trade_price']) for x in done],[float(x['trade_price']) for x in done]) if len(done)>=5 else {}
def metrics_binance(done):return base_metrics([float(x[7]) for x in done],[float(x[4]) for x in done]) if len(done)>=5 else {}

def trend_label(m):
    if not m:return 'NO_DATA'
    prev=m.get('q_vs_prev') or 0; a3=m.get('q_vs_3avg') or 0; streak=m.get('rise_streak') or 0
    # Reward genuine multi-day progression before a giant one-day spike.
    if streak>=4 and a3>=1.25:return '연속 가속형'
    if streak>=3 and a3>=1.5:return '가속형'
    if prev>=4 and streak<=2:return '일회성 폭증형'
    if a3>=1.4 and streak>=2:return '고수준 유지형'
    if prev<.75:return '감소형'
    return '중립'

def event_fields(detail):
    ev=detail.get('market_event') or {}; warning=bool(ev.get('warning',False)); caut=ev.get('caution') or {}
    active=[CAUTION_NAMES.get(k,k) for k,v in caut.items() if v]
    return warning, ','.join(active), bool(active)

def overseas_lead(um,bm):
    if not um or not bm:return ('',0.0,'')
    # Strong lead: Binance D2 had already accelerated while Upbit D2 had not; Upbit then accelerates at D1.
    b_d2=bm.get('d2_vs_prior3') or 0; u_d2=um.get('d2_vs_prior3') or 0
    u_d1=um.get('d1_vs_prior4') or 0; b_d1=bm.get('d1_vs_prior4') or 0
    if b_d2>=1.6 and u_d2<1.35 and u_d1>=1.5:
        strength=min(15,6+(b_d2-1.6)*4+min(4,max(0,u_d1-1.5)*2))
        return ('YES',round(strength,1),'Binance D2 선행→Upbit D1 확산')
    # Softer lead: Binance has a longer current acceleration streak and stronger normalized flow.
    bs=bm.get('rise_streak') or 0; us=um.get('rise_streak') or 0
    if bs>=3 and bs>us and b_d1>=1.5 and b_d1>(u_d1*1.2):
        return ('EARLY',5.0,'Binance 연속가속 우위')
    return ('',0.0,'')

def state_row(r):
    p24=r.get('upbit_price_24h_pct') or 0;p3=r.get('upbit_price_3d_pct') or 0;p5=r.get('upbit_price_5d_pct') or 0
    if r.get('warning'):return '유의종목'
    if p24>=ACTIVATED_24H_PCT or p3>=ACTIVATED_3D_PCT or p5>=ACTIVATED_5D_PCT:return '이미 발동'
    if p3>=STARTING_3D_PCT or p5>=STARTING_5D_PCT:return '시동'
    return '발사 전' if r.get('raw_signal_score',0)>=55 else '관찰'

def score_row(r):
    u3=r.get('upbit_d1_vs_3davg_x') or 0;u5=r.get('upbit_d1_vs_5davg_x') or 0;streak=r.get('upbit_rise_streak') or 0
    p24=max(0,r.get('upbit_price_24h_pct') or 0);p3=max(0,r.get('upbit_price_3d_pct') or 0);p5=max(0,r.get('upbit_price_5d_pct') or 0)
    pattern=r.get('upbit_supply_pattern','');s=0
    # Flow strength 30
    s+=min(18,max(0,(u3-1)*8));s+=min(12,max(0,(u5-1)*5))
    # Persistence 25: true consecutive increases are central to LSK-style pattern.
    s+=min(25,max(0,(streak-1)*8))
    # Price non-confirmation 25
    s+=max(0,10-p24*.8);s+=max(0,8-p3*.35);s+=max(0,7-p5*.22)
    # Overseas lead max 15
    s+=r.get('overseas_lead_score',0) or 0
    # Pattern-specific correction
    if pattern=='연속 가속형':s+=8
    elif pattern=='가속형':s+=5
    elif pattern=='일회성 폭증형':s-=18
    elif pattern=='감소형':s-=12
    # Market events
    if r.get('warning'):s-=50
    if r.get('caution_any'):s-=8
    return max(0,min(100,round(s,1)))

def qval(q,i):return q[i] if q and len(q)>i else None

def main():
    now=datetime.now(KST);stamp=now.strftime('%Y%m%d_%H%M%S');print(f'[{now:%Y-%m-%d %H:%M:%S KST}] V{VERSION} scan start')
    markets=upbit_markets();details={m['market']:m for m in markets};tickers=upbit_tickers(markets);rows=[]
    binance_status='OK'
    try:
        bx=binance_exchange_info(); b24=binance_24h()
    except Exception as e:
        bx={}; b24={}; binance_status='UNAVAILABLE: '+str(e)[:300]
        print('Binance unavailable; continuing Upbit-only:',e)
    for idx,market in enumerate(sorted(tickers)):
        t=tickers[market];base=market.split('-',1)[1]
        if float(t.get('acc_trade_price_24h',0))<MIN_UPBIT_24H_KRW:continue
        try:um=metrics_upbit(upbit_completed(upbit_daily(market),now))
        except Exception as e:print('Upbit candle skip',market,e);um={}
        bsym=bx.get(base);bm={};bt=b24.get(bsym) if bsym else None
        if bsym:
            try:bm=metrics_binance(binance_completed(binance_daily(bsym),now))
            except Exception as e:print('Binance candle skip',bsym,e)
        uq=um.get('q',[]);bq=bm.get('q',[]);detail=details.get(market,{})
        warning,caution_text,caution_any=event_fields(detail);lead,lead_score,lead_reason=overseas_lead(um,bm)
        r={'scan_time_kst':now.isoformat(timespec='seconds'),'coin':base,'upbit_market':market,'binance_symbol':bsym or '',
           'upbit_price':float(t.get('trade_price',0)),'upbit_price_24h_pct':float(t.get('signed_change_rate',0))*100,
           'upbit_24h_trade_krw':float(t.get('acc_trade_price_24h',0)),'upbit_timestamp_ms':t.get('timestamp'),
           'warning':warning,'caution_any':caution_any,'caution_types':caution_text,
           'upbit_D1_trade_krw':qval(uq,0),'upbit_D2_trade_krw':qval(uq,1),'upbit_D3_trade_krw':qval(uq,2),'upbit_D4_trade_krw':qval(uq,3),'upbit_D5_trade_krw':qval(uq,4),
           'upbit_d1_vs_prev_x':um.get('q_vs_prev'),'upbit_d1_vs_3davg_x':um.get('q_vs_3avg'),'upbit_d1_vs_5davg_x':um.get('q_vs_5avg'),
           'upbit_rise_streak':um.get('rise_streak'),'upbit_price_3d_pct':um.get('p3'),'upbit_price_5d_pct':um.get('p5'),'upbit_supply_pattern':trend_label(um),
           'binance_24h_quote_usdt':float(bt.get('quoteVolume',0)) if bt else None,'binance_price_24h_pct':float(bt.get('priceChangePercent',0)) if bt else None,
           'binance_D1_quote_usdt':qval(bq,0),'binance_D2_quote_usdt':qval(bq,1),'binance_D3_quote_usdt':qval(bq,2),'binance_D4_quote_usdt':qval(bq,3),'binance_D5_quote_usdt':qval(bq,4),
           'binance_d1_vs_prev_x':bm.get('q_vs_prev'),'binance_d1_vs_3davg_x':bm.get('q_vs_3avg'),'binance_d1_vs_5davg_x':bm.get('q_vs_5avg'),
           'binance_rise_streak':bm.get('rise_streak'),'binance_price_3d_pct':bm.get('p3'),'binance_price_5d_pct':bm.get('p5'),'binance_supply_pattern':trend_label(bm),
           'overseas_lead':lead,'overseas_lead_score':lead_score,'overseas_lead_reason':lead_reason}
        # Score first, then state uses score only to distinguish pre-launch vs watch.
        r['raw_signal_score']=score_row(r);r['state']=state_row(r);r['score']=r['raw_signal_score']
        rows.append(r)
        if idx%20==0:print(f'  processed {idx+1}/{len(tickers)}')
        time.sleep(.04)
    if not rows:raise RuntimeError('No rows collected')
    priority={'발사 전':4,'관찰':3,'시동':2,'이미 발동':1,'유의종목':0}
    rows.sort(key=lambda x:(priority.get(x['state'],0),x['score'],x['upbit_24h_trade_krw']),reverse=True)
    fields=list(rows[0].keys());full=OUT_DIR/f'scan_{stamp}.csv'
    with full.open('w',newline='',encoding='utf-8-sig') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
    top=[r for r in rows if r['state'] in ('발사 전','관찰') and not r['warning']][:30];topfile=OUT_DIR/'latest_candidates.csv'
    with topfile.open('w',newline='',encoding='utf-8-sig') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(top)
    # Stable machine-readable endpoint for ChatGPT/mobile use.
    payload={
      'scanner_version':VERSION,'generated_at_kst':now.isoformat(timespec='seconds'),
      'upbit_market_count':len(markets),'scanned_count':len(rows),
      'binance_status':binance_status,'candidate_count':len(top),
      'candidates':top[:30]
    }
    (OUT_DIR/'latest_scan.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    hist=DATA_DIR/'scan_history_v13.csv';exists=hist.exists()
    with hist.open('a',newline='',encoding='utf-8-sig') as f:w=csv.DictWriter(f,fieldnames=fields);(None if exists else w.writeheader());w.writerows(rows)
    print('\nTOP 15 - completed D1-D5 only for trend; rolling 24H kept separate')
    print('rank coin score state 24H억 D1억 D2억 D3억 streak pattern lead warning/caution')
    for i,r in enumerate(top[:15],1):
        def eok(v):return '' if v is None else round(v/1e8,2)
        evt='WARNING' if r['warning'] else (r['caution_types'] or '-')
        print(i,r['coin'],r['score'],r['state'],eok(r['upbit_24h_trade_krw']),eok(r['upbit_D1_trade_krw']),eok(r['upbit_D2_trade_krw']),eok(r['upbit_D3_trade_krw']),r['upbit_rise_streak'],r['upbit_supply_pattern'],r['overseas_lead'],evt)
    print(f'\nSaved: {topfile}\nFull: {full}\nHistory: {hist}')

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('\nERROR:',e)
        raise
