import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {DURATIONS,normalizeCandle,ledgerPath,GitHubStore,handleRequest,sameData} from "../cloudflare/worker.mjs";
const NOW=Date.parse("2026-10-03T06:15:00Z");
const env={TV_WEBHOOK_SECRET:"fixture-webhook-secret",GITHUB_TOKEN:"fixture-token"};
function candle(tf="15m") {
  const duration=DURATIONS[tf],time=Math.floor(NOW/duration)*duration-duration;
  const r={symbol:"BTCUSDT.P",timeframe:tf,time,open:100,high:110,low:90,close:105,volume:12.125,oi:900};
  if(tf!=="15m")Object.assign(r,{oi_status:"available",oi_time:time+duration,oi_time_basis:"confirmed_oi_bar_close_boundary",oi_period_start_ms:time,oi_period_end_ms:time+duration});
  return r;
}
const logger=()=>({messages:[],info(...x){this.messages.push(x);},error(...x){this.messages.push(x);}});
function github(initial={},inject=null) {
  const files=new Map(Object.entries(initial).map(([path,text])=>[path,{text,sha:"s0"}]));let count=0;const calls=[];
  async function fetcher(url,options) {
    const path=new URL(url).pathname.split("/contents/")[1].split("/").map(decodeURIComponent).join("/");calls.push({path,method:options.method});
    if(options.method==="GET") {
      if(!files.has(path))return new Response("{}",{status:404});
      const file=files.get(path);return Response.json({sha:file.sha,encoding:"base64",content:Buffer.from(file.text).toString("base64")});
    }
    const body=JSON.parse(options.body);const existing=files.get(path);
    if(inject) {const response=inject({path,body,files,calls});if(response)return response;}
    if((existing?.sha??undefined)!==body.sha)return new Response("{}",{status:409});
    files.set(path,{text:Buffer.from(body.content,"base64").toString("utf8"),sha:"s"+(++count)});
    return Response.json({content:{sha:"s"+count}});
  }
  return {files,calls,fetcher};
}
async function request(payload,gh=github(),log=logger()) {
  const jobs=[];
  const response=await handleRequest(new Request("https://worker.invalid/webhook",{method:"POST",body:JSON.stringify({secret:env.TV_WEBHOOK_SECRET,...payload})}),env,{waitUntil(job){jobs.push(job);}},{fetcher:gh.fetcher,now:()=>NOW,logger:log});
  await Promise.all(jobs);return {response,gh,log};
}
test("legacy 15m exact fields and paths, no secret persistence",async()=>{
  const input=candle();const {response,gh}=await request(input);
  assert.equal(response.status,200);assert.equal(gh.files.size,2);
  const saved=JSON.parse(gh.files.get("output/btc_anytime_webhook_latest.json").text);
  assert.deepEqual(Object.keys(saved),["received_at_utc","symbol","timeframe","time","candle_time_utc","open","high","low","close","volume","oi"]);
  assert.equal(saved.time,input.time);assert.equal(saved.oi,input.oi);
  assert.ok(gh.files.has(ledgerPath(input)));
  for(const f of gh.files.values())assert.ok(!f.text.includes(env.TV_WEBHOOK_SECRET));
});
test("four timeframes use separate UTC files/latest and one HTTP response",async()=>{
  const {response,gh}=await request({...candle(),higher_timeframe_candles:[candle("1h"),candle("4h"),candle("1d")]});
  assert.equal(response.status,200);assert.equal(gh.files.size,8);
  for(const tf of ["1h","4h","1d"])assert.ok(gh.files.has(`output/btc_anytime_${tf}_latest.json`));
  assert.equal(JSON.parse(gh.files.get("output/btc_anytime_webhook_latest.json").text).timeframe,"15m");
});
test("future/in-progress, boundary, negative, non-finite and OHLC rejection",()=>{
  for(const patch of [{time:NOW},{time:candle("1h").time+1},{is_closed:false},{volume:-1},{oi:-1},{open:NaN},{high:99},{low:106},{oi:null},{oi:true},{timeframe:"2h"},{symbol:"ETHUSDT.P"}])assert.throws(()=>normalizeCandle({...candle("1h"),...patch},NOW));
});
test("exact candle end is accepted but one millisecond before is rejected",()=>{
  const r=candle("1h");assert.doesNotThrow(()=>normalizeCandle(r,r.time+3600000));assert.throws(()=>normalizeCandle(r,r.time+3599999));
});
test("HTF explicit OI unavailable is accepted, missing/invalid OI is not",()=>{
  const r=candle("1h");assert.doesNotThrow(()=>normalizeCandle({...r,oi:null,oi_status:"unavailable",oi_unavailable_reason:"feed_missing"},NOW));
  assert.throws(()=>normalizeCandle({...r,oi:null},NOW));assert.throws(()=>normalizeCandle({...r,oi_time:r.time},NOW));assert.throws(()=>normalizeCandle({...r,oi_status:"unavailable"},NOW));
});
test("invalid HTF does not prevent legacy 15m persistence",async()=>{
  const {response,gh}=await request({...candle(),higher_timeframe_candles:[{...candle("1h"),time:NOW}]});
  assert.equal(response.status,200);assert.equal(gh.files.size,2);
});
test("wrong secret causes no GitHub writes or raw payload logs",async()=>{
  const {response,gh,log}=await request({...candle(),secret:"incorrect"});assert.equal(response.status,401);assert.equal(gh.calls.length,0);assert.deepEqual(log.messages,[["Rejected: invalid webhook secret"]]);
});
test("duplicate same key/data is skipped including collection-time differences",async()=>{
  const r=normalizeCandle(candle("1h"),NOW);const gh=github({[ledgerPath(r)]:JSON.stringify(r)+"\n","output/btc_anytime_1h_latest.json":JSON.stringify(r)});
  await request(candle("1h"),gh);assert.equal(gh.calls.filter(c=>c.method==="PUT").length,0);
});
test("conflict preserves ledger and latest",async()=>{
  const r=normalizeCandle(candle("1h"),NOW),text=JSON.stringify(r)+"\n";const gh=github({[ledgerPath(r)]:text});
  const {log}=await request({...candle("1h"),close:104},gh);assert.equal(gh.files.get(ledgerPath(r)).text,text);assert.equal(gh.calls.filter(c=>c.method==="PUT").length,0);assert.ok(log.messages.some(x=>x[0]==="candle_conflict"));
});
test("duplicate in immutable bootstrap is never appended",async()=>{
  const r=normalizeCandle(candle("1h"),NOW);r.symbol="BTCUSDT";
  const gh=github({"data_market/btc_anytime/1h/btc_1h_history.jsonl":JSON.stringify(r)+"\n"});
  await request(candle("1h"),gh);assert.ok(!gh.files.has(ledgerPath(r)));
});
test("SHA retry re-GET finds racing same candle and skips instead of duplicating",async()=>{
  let raced=false;const input=candle("1h"),r=normalizeCandle(input,NOW);
  const gh=github({},({path,files})=>{if(path===ledgerPath(r)&&!raced){raced=true;files.set(path,{text:JSON.stringify(r)+"\n",sha:"racing"});return new Response("{}",{status:409});}});
  await request(input,gh);assert.equal(gh.files.get(ledgerPath(r)).text.trim().split("\n").length,1);
  assert.ok(gh.calls.filter(c=>c.path===ledgerPath(r)&&c.method==="GET").length>=2);
});
test("SHA retry re-GET finds conflicting candle and logs conflict",async()=>{
  let raced=false;const input=candle("1h"),r=normalizeCandle({...input,close:104},NOW);
  const gh=github({},({path,files})=>{if(path===ledgerPath(r)&&!raced){raced=true;files.set(path,{text:JSON.stringify(r)+"\n",sha:"racing"});return new Response("{}",{status:422});}});
  const {log}=await request(input,gh);assert.equal(JSON.parse(gh.files.get(ledgerPath(r)).text).close,104);assert.ok(log.messages.some(x=>x[0]==="candle_conflict"));assert.ok(!gh.files.has("output/btc_anytime_webhook_latest.json"));
});
test("persistent SHA conflicts retry only three times",async()=>{
  const gh=github({},()=>new Response("{}",{status:409}));const {log}=await request(candle("1h"),gh);
  assert.equal(gh.calls.filter(c=>c.method==="PUT").length,3);assert.ok(log.messages.some(x=>x[1]?.code==="github_retry_exhausted"));
});
test("older out-of-order insert preserves existing row text and monotonic latest",async()=>{
  const newer=normalizeCandle(candle("1h"),NOW),older={...candle("1h"),time:candle("1h").time-3600000,oi_time:candle("1h").oi_time-3600000,oi_period_start_ms:candle("1h").oi_period_start_ms-3600000,oi_period_end_ms:candle("1h").oi_period_end_ms-3600000};const text=JSON.stringify(newer);
  const gh=github({[ledgerPath(newer)]:text+"\n","output/btc_anytime_1h_latest.json":text});await request(older,gh);
  const lines=gh.files.get(ledgerPath(newer)).text.trim().split("\n");assert.equal(lines[1],text);assert.equal(JSON.parse(lines[0]).time,older.time);assert.equal(gh.files.get("output/btc_anytime_1h_latest.json").text,text);
});
test("HTTP 200 does not wait for GitHub completion",async()=>{
  let resolve;const blocked=new Promise(r=>{resolve=r;});const jobs=[];
  const response=await handleRequest(new Request("https://worker.invalid",{method:"POST",body:JSON.stringify({secret:env.TV_WEBHOOK_SECRET,...candle()})}),env,{waitUntil(p){jobs.push(p);}},{fetcher:async()=>{await blocked;return new Response("{}",{status:404});},now:()=>NOW,logger:logger()});
  assert.equal(response.status,200);assert.equal(jobs.length,1);resolve();await Promise.allSettled(jobs);
});
test("malformed ledger fails without overwriting data",async()=>{
  const input=candle("1h"),gh=github({[ledgerPath(input)]:"broken-json\n"});await request(input,gh);assert.equal(gh.calls.filter(c=>c.method==="PUT").length,0);
});
test("batch envelope support and UTC midnight partition",async()=>{
  const day=candle("1d");assert.ok(ledgerPath(day).endsWith("btc_1d_20261002.jsonl"));
  const {response}=await request({candles:[day]});assert.equal(response.status,200);
});
test("OI time basis differences are conflicts even when numeric OI matches",()=>{
  const a=normalizeCandle(candle(),NOW);assert.ok(!sameData(a,{...a,oi_time_basis:"different"}));
});
test("Pine static guard: offsets inside requests, one alert, no aggregation",()=>{
  const pine=readFileSync(new URL("../tradingview/btc_anytime_htf.pine",import.meta.url),"utf8");
  assert.equal((pine.match(/alert\(msg/g)||[]).length,1);
  assert.equal((pine.match(/lookahead=barmerge.lookahead_on/g)||[]).length,6);
  assert.ok(pine.includes("[open[1], high[1], low[1], close[1], volume[1], time[1], time_close[1]]"));
  assert.ok(pine.includes("higher_timeframe_candles"));assert.ok(pine.includes("barstate.isrealtime and barstate.isconfirmed"));
});
