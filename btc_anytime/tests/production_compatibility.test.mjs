import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import {readFileSync} from "node:fs";
import {handleRequest} from "../cloudflare/worker.mjs";
const source=readFileSync(new URL("production_worker.fixture.mjs",import.meta.url),"utf8").replace("export default", "globalThis.productionWorker =");
const NOW=Date.parse("2026-10-03T06:15:00Z");
const env={TV_WEBHOOK_SECRET:"fixture-secret",GITHUB_TOKEN:"fixture-token"};
const latest="output/btc_anytime_webhook_latest.json";
const base=()=>({secret:env.TV_WEBHOOK_SECRET,symbol:"BTCUSDT.P",timeframe:"15m",time:NOW-900000,open:100,high:110,low:90,close:105,volume:12.125,oi:900});
class FixedDate extends Date {constructor(...args){super(...(args.length?args:[NOW]));}static now(){return NOW;}}
async function run(production,{method="POST",payload=base(),raw,initial={},fail,bindings=env}={}) {
  const files=new Map(Object.entries(initial).map(([p,text])=>[p,{text,sha:"s0"}]));
  const calls=[],logs=[],jobs=[];let writes=0;
  const logger={log(...args){logs.push(args);},info(...args){logs.push(args);},error(...args){logs.push(args);}};
  const fetcher=async(url,options)=>{
    const path=new URL(url).pathname.split("/contents/")[1];
    const body=options.body?JSON.parse(options.body):undefined;
    calls.push({url,method:options.method,headers:{...options.headers},...(body?{body}:{})});
    if(fail?.method===options.method && (!fail.path || fail.path===path))return new Response(fail.text||"fixture GitHub error",{status:fail.status});
    if(options.method==="GET")return files.has(path)?Response.json({sha:files.get(path).sha,content:Buffer.from(files.get(path).text).toString("base64"),encoding:"base64"}):new Response("{}",{status:404});
    const existing=files.get(path);
    if((existing?.sha??undefined)!==body.sha)return new Response("fixture SHA conflict",{status:409});
    files.set(path,{text:Buffer.from(body.content,"base64").toString("utf8"),sha:"s"+(++writes)});
    return Response.json({content:{sha:"s"+writes}});
  };
  const context={waitUntil(p){jobs.push(p);}};
  const req=new Request("https://fixture.invalid",{method,...(!["GET","HEAD"].includes(method)?{body:raw??JSON.stringify(payload)}:{})});
  let response;
  if(production){const sandbox={Date:FixedDate,console:logger,fetch:fetcher,TextEncoder,TextDecoder,Uint8Array,btoa,atob,Response,Number,JSON};vm.runInNewContext(source,sandbox);response=await sandbox.productionWorker.fetch(req,bindings,context);}
  else response=await handleRequest(req,bindings,context,{now:()=>NOW,fetcher,logger});
  const status=response.status,text=await response.text(),contentType=response.headers.get("content-type");
  const results=await Promise.allSettled(jobs);
  return {status,text,contentType,files:[...files],calls,logs,background:results.map(r=>({status:r.status,...(r.status==="rejected"?{error:r.reason.message}:{})}))};
}
async function equal(options){const old=await run(true,options),current=await run(false,options);assert.deepEqual(current,old);return current;}
for(const method of ["GET","HEAD","DELETE"])test("production parity: non-POST "+method,()=>equal({method}));
test("production parity: normal flat payload, exact calls/bytes/messages/logs",async()=>{const result=await equal();assert.equal(result.status,200);assert.deepEqual(JSON.parse(result.text),{ok:true,accepted:true});assert.deepEqual(result.calls.map(c=>c.method),["GET","PUT","GET","PUT"]);assert.equal(result.calls[1].body.message,"Update BTC Anytime latest data");});
for(const [name,patch] of [["wrong secret",{secret:"wrong"}],["missing secret",{secret:undefined}],["wrong symbol",{symbol:"ETHUSDT.P"}],["wrong timeframe",{timeframe:"2h"}],["null OHLC",{open:null}],["numeric string",{volume:"12"}],["boolean OI",{oi:true}],["missing time",{time:undefined}]])test("production parity: "+name,()=>equal({payload:{...base(),...patch}}));
for(const raw of ["{", "null"])test("production parity: malformed/null request "+raw,()=>equal({raw}));
for(const [name,patch] of [["unaligned time",{time:NOW-899999}],["future time",{time:NOW+900000}],["negative volume/OI",{volume:-1,oi:-2}],["inconsistent OHLC",{high:1}],["extra metadata ignored",{oi_time:123,is_closed:false,source:"ignored",extra:"x"}],["oversized extra field",{extra:"x".repeat(20000)}]])test("production parity: original finite-only policy "+name,()=>equal({payload:{...base(),...patch}}));
const path="data_market/btc_anytime/15m/btc_15m_20261003.jsonl";
const stored={...base(),received_at_utc:"2026-10-03T06:01:00Z",candle_time_utc:new Date(base().time).toISOString()};delete stored.secret;
test("production parity: duplicate skips ledger but updates latest",async()=>{const r=await equal({initial:{[path]:JSON.stringify(stored)+"\n",[latest]:JSON.stringify(stored)}});assert.equal(r.calls.filter(c=>c.method==="PUT").length,1);});
test("production parity: same time changed values still skip ledger",()=>equal({initial:{[path]:JSON.stringify({...stored,close:104})+"\n"}}));
test("production parity: older candle appends and replaces latest",()=>equal({initial:{[path]:JSON.stringify({...stored,time:NOW})+"\n",[latest]:JSON.stringify({...stored,time:NOW})}}));
test("production parity: damaged JSONL row is retained and append continues",()=>equal({initial:{[path]:"broken-json\n"}}));
test("production parity: UTF-8 and missing terminal newline preserved",()=>equal({initial:{[path]:JSON.stringify({...stored,time:NOW-1800000,note:"한글 🚀"})}}));
test("production parity: bootstrap does not change legacy duplicate policy",()=>equal({initial:{"data_market/btc_anytime/15m/btc_15m_history.jsonl":JSON.stringify(stored)+"\n"}}));
for(const fail of [{method:"GET",status:403},{method:"PUT",status:409},{method:"PUT",status:422},{method:"PUT",path,status:500},{method:"GET",path,status:403}])test("production parity: GitHub failure "+JSON.stringify(fail),async()=>{const r=await equal({fail});assert.equal(r.status,200);assert.equal(r.background[0].status,"rejected");});
test("production parity: missing token fails asynchronously",()=>equal({bindings:{TV_WEBHOOK_SECRET:env.TV_WEBHOOK_SECRET},fail:{method:"GET",status:401}}));
test("production parity: repository overrides do not redirect 15m",()=>equal({bindings:{...env,GITHUB_REPOSITORY:"other/repo",GITHUB_BRANCH:"other"}}));
test("invalid/oversized HTF extension leaves original 15m persistence unchanged",async()=>{for(const higher_timeframe_candles of [[{timeframe:"1h",time:NOW}],Array(10).fill({})]){const actual=await run(false,{payload:{...base(),higher_timeframe_candles}}),original=await run(true);assert.deepEqual(actual.calls,original.calls);assert.deepEqual(actual.files,original.files);assert.equal(actual.text,original.text);}});
test("legacy background failure does not prevent independent HTF save",async()=>{
  const duration=3600000,time=Math.floor(NOW/duration)*duration-duration;
  const ht={symbol:"BTCUSDT.P",timeframe:"1h",time,open:100,high:110,low:90,close:105,volume:1,oi:null,oi_status:"unavailable",oi_unavailable_reason:"fixture"};
  const r=await run(false,{payload:{...base(),higher_timeframe_candles:[ht]},fail:{method:"PUT",path:latest,status:500}});
  assert.equal(r.status,200);assert.equal(r.background[0].status,"rejected");assert.ok(r.files.some(([p])=>p==="output/btc_anytime_1h_latest.json"));
});
