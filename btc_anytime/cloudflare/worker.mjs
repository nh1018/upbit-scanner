// Candidate only. No deployment is performed by this repository.
export const DURATIONS = Object.freeze({"15m":900000,"1h":3600000,"4h":14400000,"1d":86400000});
const CORE = ["open","high","low","close","volume"];
const MAX_BODY = 16384;
const MAX_ATTEMPTS = 3;
const encoder = new TextEncoder();
function finite(value) { return typeof value === "number" && Number.isFinite(value); }
function authEqual(a,b) {
  if (typeof a !== "string" || typeof b !== "string" || !a || !b) return false;
  const x=encoder.encode(a), y=encoder.encode(b); let different=x.length^y.length;
  for(let i=0;i<Math.max(x.length,y.length);i++) different|=(x[i]||0)^(y[i]||0);
  return different===0;
}
function reject(code) { const e=new Error(code); e.code=code; throw e; }
function validUtc(value) { return typeof value==="string" && Number.isFinite(Date.parse(value)); }
function legacyLog(logger,...args) { (logger.log||logger.info||console.log).apply(logger,args); }
function legacyValidation(data) {
  return data.symbol==="BTCUSDT.P" && data.timeframe==="15m" &&
    ["time",...CORE,"oi"].every(key=>Number.isFinite(data[key]));
}
function legacySafeData(data,nowMs) {
  const safe={received_at_utc:new Date(nowMs).toISOString(),symbol:data.symbol,timeframe:data.timeframe,time:data.time,candle_time_utc:new Date(data.time).toISOString()};
  for(const key of [...CORE,"oi"])safe[key]=data[key];
  return safe;
}
export function normalizeCandle(input, nowMs=Date.now()) {
  if (!input || typeof input!=="object" || Array.isArray(input)) reject("invalid_candle");
  if(input.timeframe==="15m") {if(!legacyValidation(input))reject("invalid_market_data");return legacySafeData(input,nowMs);}
  const duration=DURATIONS[input.timeframe];
  if(input.symbol!=="BTCUSDT.P" || !duration) reject("unsupported_market");
  if(!Number.isSafeInteger(input.time) || input.time<0 || input.time%duration!==0) reject("invalid_boundary");
  const end=input.time+duration;
  if(end>nowMs || input.is_closed===false) reject("in_progress");
  if(input.close_time_ms!==undefined && input.close_time_ms!==end-1) reject("invalid_close_time");
  if(input.candle_time_utc!==undefined && Date.parse(input.candle_time_utc)!==input.time) reject("invalid_candle_time");
  for(const key of CORE) if(!finite(input[key])) reject("invalid_"+key);
  if(input.volume<0) reject("negative_volume");
  if(!(input.high>=input.open && input.high>=input.close && input.low<=input.open && input.low<=input.close && input.high>=input.low)) reject("invalid_ohlc");
  const oiUnavailable=input.oi===null && input.oi_status==="unavailable";
  if(!finite(input.oi) && !(input.timeframe!=="15m" && oiUnavailable)) reject("invalid_oi");
  if(finite(input.oi) && input.oi<0) reject("negative_oi");
  if(finite(input.oi) && input.oi_status==="unavailable") reject("contradictory_oi_status");
  if(input.emitted_at_utc!==undefined && (!validUtc(input.emitted_at_utc) || Date.parse(input.emitted_at_utc)<end || Date.parse(input.emitted_at_utc)>nowMs+5000)) reject("invalid_emitted_time");
  const data={received_at_utc:new Date(nowMs).toISOString(),symbol:input.symbol,timeframe:input.timeframe,time:input.time,candle_time_utc:new Date(input.time).toISOString()};
  for(const key of [...CORE,"oi"]) data[key]=input[key];
  if(input.timeframe!=="15m") {
    data.market="BINANCE_USDT_M_FUTURES"; data.schema_version="btc-anytime-htf-v1";
    data.close_time_ms=end-1; data.is_closed=true;
    data.source="tradingview_binance_usdm_htf";
    data.oi_status=oiUnavailable?"unavailable":"available";
    if(!oiUnavailable) {
      if(input.oi_period_start_ms!==input.time || input.oi_period_end_ms!==end || input.oi_time!==end || input.oi_time_basis!=="confirmed_oi_bar_close_boundary") reject("oi_period_mismatch");
    } else if(typeof input.oi_unavailable_reason!=="string" || !input.oi_unavailable_reason) reject("missing_oi_reason");
  }
  // Optional metadata is whitelisted; the secret is never copied or logged.
  for(const key of ["oi_time","oi_time_basis","oi_period_start_ms","oi_period_end_ms","oi_status","oi_unavailable_reason","emitted_at_utc"]) if(input[key]!==undefined) data[key]=input[key];
  if(input.timeframe==="15m" && input.oi_time!==undefined) {
    if(input.oi_time!==end || input.oi_period_start_ms!==input.time || input.oi_period_end_ms!==end || input.oi_time_basis!=="confirmed_oi_bar_close_boundary") reject("oi_period_mismatch");
  }
  return data;
}
export function ledgerPath(candle) {
  const day=new Date(candle.time).toISOString().slice(0,10).replaceAll("-","");
  return `data_market/btc_anytime/${candle.timeframe}/btc_${candle.timeframe}_${day}.jsonl`;
}
function numericEqual(a,b) {
  if(a===null || a===undefined || b===null || b===undefined) return (a??null)===(b??null);
  return (typeof a==="number" || typeof a==="string") && (typeof b==="number" || typeof b==="string") && String(a).trim()!=="" && String(b).trim()!=="" && Number.isFinite(Number(a)) && Number(a)===Number(b);
}
export function sameData(a,b) {
  // BTCUSDT and BTCUSDT.P both identify this one allowed USD-M futures market.
  if(!["BTCUSDT","BTCUSDT.P"].includes(a.symbol) || !["BTCUSDT","BTCUSDT.P"].includes(b.symbol) || a.time!==b.time || a.timeframe!==b.timeframe) return false;
  return [...CORE,"oi"].every(k=>numericEqual(a[k],b[k])) &&
    ["oi_time","oi_time_basis","oi_period_start_ms","oi_period_end_ms","oi_timestamp_ms","oi_alignment"].every(k=>(a[k]??null)===(b[k]??null));
}
function decodeBase64(content) { return new TextDecoder().decode(Uint8Array.from(atob(content.replaceAll("\n","")),c=>c.charCodeAt(0))); }
function encodeBase64(content) { const bytes=encoder.encode(content); let s=""; for(let i=0;i<bytes.length;i+=8192) s+=String.fromCharCode(...bytes.subarray(i,i+8192)); return btoa(s); }
function diagnosticText(value,env) {
  let text=typeof value==="string"?value:"";
  for(const [key,secret] of Object.entries(env)) if(/token|secret|password|api.?key/i.test(key) && typeof secret==="string" && secret) text=text.split(secret).join("[REDACTED]");
  return text.replace(/Bearer\s+[^\s,;]+/gi,"Bearer [REDACTED]").replace(/(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]+/g,"[REDACTED]").replace(/(authorization|token|secret|api[_-]?key)\s*[:=]\s*[^\s,;]+/gi,"$1=[REDACTED]").slice(0,500);
}
function parseLedger(content) {
  const lines=content.split(/\r?\n/).filter(s=>s.trim());
  return lines.map(line=>({line,row:JSON.parse(line)}));
}
export class GitHubStore {
  constructor(env,fetcher=fetch,now=()=>Date.now(),logger=console) {
    if(!env.GITHUB_TOKEN) reject("missing_github_token");
    const repo=env.GITHUB_REPOSITORY||"nh1018/upbit-scanner";
    if(!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo)) reject("invalid_repository");
    this.base=`https://api.github.com/repos/${repo}/contents/`;
    this.branch=env.GITHUB_BRANCH||"main"; this.token=env.GITHUB_TOKEN;
    this.fetcher=fetcher;this.now=now;this.logger=logger;this.deadline=now()+25000;
    this.diagnostic={stage:"store_init",method:null,path:null,retry_attempt:0};
  }
  mark(stage,method,path,attempt) {this.diagnostic={stage,method,path,retry_attempt:attempt+1};}
  async api(method,path,body) {
    const remaining=this.deadline-this.now(); if(remaining<=0) reject("github_deadline");
    const ctrl=new AbortController(),timer=setTimeout(()=>ctrl.abort(),Math.min(4000,remaining));
    try {
      const url=this.base+path.split("/").map(encodeURIComponent).join("/")+(method==="GET"?`?ref=${encodeURIComponent(this.branch)}`:"");
      const response=await this.fetcher.call(globalThis,url,{method,headers:{Authorization:`Bearer ${this.token}`,Accept:"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28","User-Agent":"btc-anytime-htf","Content-Type":"application/json"},body:body?JSON.stringify(body):undefined,signal:ctrl.signal});
      const content=await response.arrayBuffer();
      return new Response([204,205,304].includes(response.status)?null:content,{status:response.status,headers:response.headers});
    } finally {clearTimeout(timer);}
  }
  async get(path) {
    const response=await this.api("GET",path);
    if(response.status===404) return {sha:null,text:""};
    if(!response.ok) reject("github_get_"+response.status);
    const doc=await response.json();
    if(typeof doc.content!=="string" || doc.encoding!=="base64") reject("github_file_too_large_or_invalid");
    return {sha:doc.sha,text:decodeBase64(doc.content)};
  }
  async put(path,file,text,message) {
    return this.api("PUT",path,{message,content:encodeBase64(text),branch:this.branch,...(file.sha?{sha:file.sha}:{})});
  }
  async append(candle) {
    const path=ledgerPath(candle),historyPath=`data_market/btc_anytime/${candle.timeframe}/btc_${candle.timeframe}_history.jsonl`;
    for(let attempt=0;attempt<MAX_ATTEMPTS;attempt++) {
      // Re-read BOTH history and target after a SHA conflict.
      this.mark("history_get","GET",historyPath,attempt);
      const history=await this.get(historyPath);
      this.mark("daily_get","GET",path,attempt);
      const file=await this.get(path);
      this.mark("history_parse",null,historyPath,attempt);
      const historicalRows=parseLedger(history.text);
      this.mark("daily_parse",null,path,attempt);
      const dailyRows=parseLedger(file.text);
      this.mark("duplicate_conflict_check",null,path,attempt);
      const matches=[...historicalRows,...dailyRows].filter(r=>r.row.time===candle.time);
      if(matches.length) {
        if(matches.length!==1 || !sameData(matches[0].row,candle)) {this.logger.error("candle_conflict",{path,time:candle.time,timeframe:candle.timeframe});return "conflict";}
        return "duplicate";
      }
      this.mark("daily_parse",null,path,attempt);
      const rows=parseLedger(file.text);rows.push({row:candle,line:JSON.stringify(candle)});
      rows.sort((a,b)=>a.row.time-b.row.time);
      // Preserve existing JSONL row bytes; only a new row is inserted.
      const text=rows.map(r=>r.line).join("\n")+"\n";
      this.mark("daily_put","PUT",path,attempt);
      const response=await this.put(path,file,text,`Append BTC ${candle.timeframe} candle ${candle.candle_time_utc}`);
      if(response.ok) return "inserted";
      if(![409,422].includes(response.status)) reject("github_put_"+response.status);
    }
    reject("github_retry_exhausted");
  }
  async latest(candle) {
    const path=candle.timeframe==="15m"?"output/btc_anytime_webhook_latest.json":`output/btc_anytime_${candle.timeframe}_latest.json`;
    for(let attempt=0;attempt<MAX_ATTEMPTS;attempt++) {
      this.mark("latest_get","GET",path,attempt);
      const file=await this.get(path);
      if(file.text) {
        this.mark("latest_parse",null,path,attempt);
        const old=JSON.parse(file.text);
        if(old.time>candle.time) return "older_skipped";
        if(old.time===candle.time) {
          if(!sameData(old,candle)) this.logger.error("latest_conflict",{path,time:candle.time,timeframe:candle.timeframe});
          return "same_key_skipped";
        }
      }
      this.mark("latest_put","PUT",path,attempt);
      const response=await this.put(path,file,JSON.stringify(candle,null,2)+"\n",`Update BTC Anytime ${candle.timeframe} latest data`);
      if(response.ok) return "updated";
      if(![409,422].includes(response.status)) reject("github_put_"+response.status);
    }
    reject("github_retry_exhausted");
  }
}
export async function saveBatch(candles,env,options={}) {
  const {fetcher=fetch,now=()=>Date.now(),logger=console}=options;
  let legacyError=null,store;
  for(const candle of candles) {
    if(candle.timeframe==="15m") {
      try {await saveLegacy15m(candle,env,options);}catch(error){legacyError=error;}
      continue;
    }
    try {
      store??=new GitHubStore(env,fetcher,now,logger);
      const outcome=await store.append(candle);
      if(outcome!=="conflict")await store.latest(candle);
      logger.info("candle_save",{timeframe:candle.timeframe,time:candle.time,outcome});
    }catch(e){
      const name=diagnosticText(e?.name,env),message=diagnosticText(e?.message,env);
      logger.error("candle_save_failed",{timeframe:candle.timeframe,time:candle.time,candle_time:candle.candle_time_utc,
        code:diagnosticText(e?.code||"network_or_parse_error",env),...(store?.diagnostic||{stage:"store_init",method:null,path:null,retry_attempt:0}),
        exception_name:name,exception_message:message,abort:name==="AbortError",timeout:name==="TimeoutError"||name==="AbortError"||e?.code==="github_deadline",
        request_elapsed_ms:Math.max(0,now()-(options.requestStartedMs??(store?store.deadline-25000:now())))});
    }
  }
  if(legacyError)throw legacyError;
}
function jsonReply(data,status) {return new Response(JSON.stringify(data),{status,headers:{"Content-Type":"application/json"}});}
export async function handleRequest(request,env,ctx,options={}) {
  if(request.method!=="POST")return new Response("BTC Anytime Webhook is running",{status:200});
  const logger=options.logger||console;
  try {
    const data=await request.json();
    if(!data.secret || data.secret!==env.TV_WEBHOOK_SECRET) {
      legacyLog(logger,"Rejected: invalid webhook secret");
      return jsonReply({ok:false,error:"Unauthorized"},401);
    }
    legacyLog(logger,"Validation diagnostic:",JSON.stringify({symbol:data.symbol,timeframe:data.timeframe,
      time_ok:Number.isFinite(data.time),open_ok:Number.isFinite(data.open),high_ok:Number.isFinite(data.high),low_ok:Number.isFinite(data.low),close_ok:Number.isFinite(data.close),volume_ok:Number.isFinite(data.volume),oi_ok:Number.isFinite(data.oi)}));
    const nowMs=(options.now||Date.now)();let candles=[];
    const hasLegacy=data.timeframe==="15m";
    if(hasLegacy) {
      if(!legacyValidation(data)) {
        legacyLog(logger,"Rejected: invalid market data");
        return jsonReply({ok:false,error:"Invalid market data"},400);
      }
      candles.push(legacySafeData(data,nowMs));
    }
    if(!hasLegacy && !Array.isArray(data.candles) && !["1h","4h","1d"].includes(data.timeframe)) {
      legacyLog(logger,"Rejected: invalid market data");
      return jsonReply({ok:false,error:"Invalid market data"},400);
    }
    const additions=hasLegacy?data.higher_timeframe_candles:(Array.isArray(data.candles)?data.candles:[data]);
    if(Array.isArray(additions)) {
      if(additions.length> (hasLegacy?3:4))logger.error("htf_batch_rejected",{code:"invalid_batch_size"});
      else {
        const keys=new Set(candles.map(c=>c.timeframe+":"+c.time));
        for(const input of additions)try {
          if(hasLegacy && input?.timeframe==="15m")reject("htf_only_extension");
          const candle=normalizeCandle(input,nowMs),key=candle.timeframe+":"+candle.time;
          if(keys.has(key))reject("duplicate_batch_key");keys.add(key);candles.push(candle);
        }catch(e){logger.error("candle_rejected",{code:e.code||"invalid_candle"});}
      }
    }
    if(!candles.length) {
      legacyLog(logger,"Rejected: invalid market data");
      return jsonReply({ok:false,error:"Invalid market data"},400);
    }
    candles.sort((a,b)=>(a.timeframe==="15m"?-1:0)-(b.timeframe==="15m"?-1:0));
    ctx.waitUntil(saveBatch(candles,env,{...options,requestStartedMs:nowMs}));
    legacyLog(logger,"TradingView webhook accepted");
    for(const candle of candles)legacyLog(logger,JSON.stringify(candle));
    return jsonReply({ok:true,accepted:true},200);
  }catch(error) {
    legacyLog(logger,"Worker request error: "+error.message);
    return jsonReply({ok:false,error:"Internal error"},500);
  }
}
export default {fetch:handleRequest};

// Production 15m persistence retained independently from HTF storage.
export async function saveLegacy15m(safeData, env, {fetcher=fetch,logger=console}={}) {
  try {
    const owner = "nh1018";
    const repo = "upbit-scanner";
    const branch = "main";

    const headers = {
      "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "btc-anytime-cloudflare-worker"
    };

    async function getGitHubFile(path) {
      const apiUrl =
        `https://api.github.com/repos/${owner}/${repo}/contents/${path}?ref=${branch}`;

      const response = await fetcher(apiUrl, {
        method: "GET",
        headers
      });

      if (response.status === 404) {
        return null;
      }

      if (!response.ok) {
        const errorText = await response.text();

        throw new Error(
          `GitHub GET failed: ${response.status} ${errorText}`
        );
      }

      return await response.json();
    }


    function textToBase64(text) {
      const bytes = new TextEncoder().encode(text);
      let binary = "";

      for (const byte of bytes) {
        binary += String.fromCharCode(byte);
      }

      return btoa(binary);
    }


    function base64ToText(base64) {
      const binary = atob(base64.replace(/\n/g, ""));
      const bytes = new Uint8Array(binary.length);

      for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
      }

      return new TextDecoder().decode(bytes);
    }


    async function putGitHubFile(path, text, message, sha = null) {
      const apiUrl =
        `https://api.github.com/repos/${owner}/${repo}/contents/${path}`;

      const body = {
        message,
        content: textToBase64(text),
        branch
      };

      if (sha) {
        body.sha = sha;
      }

      const response = await fetcher(apiUrl, {
        method: "PUT",
        headers: {
          ...headers,
          "Content-Type": "application/json"
        },
        body: JSON.stringify(body)
      });

      if (!response.ok) {
        const errorText = await response.text();

        throw new Error(
          `GitHub PUT failed: ${response.status} ${errorText}`
        );
      }

      return await response.json();
    }


    // ===== latest =====

    const latestPath =
      "output/btc_anytime_webhook_latest.json";

    const latestExisting =
      await getGitHubFile(latestPath);

    const latestText =
      JSON.stringify(safeData, null, 2) + "\n";

    await putGitHubFile(
      latestPath,
      latestText,
      "Update BTC Anytime latest data",
      latestExisting ? latestExisting.sha : null
    );


    // ===== 15m History =====

    const candleDate =
      new Date(safeData.time)
        .toISOString()
        .slice(0, 10)
        .replace(/-/g, "");

    const historyPath =
      `data_market/btc_anytime/15m/btc_15m_${candleDate}.jsonl`;

    const historyExisting =
      await getGitHubFile(historyPath);

    let historyText = "";
    let historySha = null;

    if (historyExisting) {
      historySha = historyExisting.sha;
      historyText =
        base64ToText(historyExisting.content);
    }


    // 중복 검사
    let duplicate = false;

    if (historyText.trim()) {
      const lines =
        historyText.trim().split("\n");

      for (const line of lines) {
        try {
          const row = JSON.parse(line);

          if (row.time === safeData.time) {
            duplicate = true;
            break;
          }
        } catch (_) {
          // 기존 손상 행 때문에 전체 저장 중단하지 않음
        }
      }
    }


    // 신규 확정 15분봉만 추가
    if (!duplicate) {
      if (
        historyText.length > 0 &&
        !historyText.endsWith("\n")
      ) {
        historyText += "\n";
      }

      historyText +=
        JSON.stringify(safeData) + "\n";

      await putGitHubFile(
        historyPath,
        historyText,
        `Append BTC 15m candle ${safeData.candle_time_utc}`,
        historySha
      );

      legacyLog(logger,"BTC 15m history appended");

    } else {
      legacyLog(logger,"BTC 15m duplicate skipped");
    }

    legacyLog(logger,"GitHub latest file updated");

  } catch (error) {
    // TradingView 응답과 분리:
    // GitHub 저장 실패는 로그에 명확하게 남김
    legacyLog(logger,
      "GitHub background save failed: " +
      error.message
    );

    throw error;
  }
}