import test from "node:test";
import assert from "node:assert/strict";
import {normalizeCandle,saveBatch} from "../cloudflare/worker.mjs";
const NOW=Date.parse("2026-10-03T09:30:00Z"),time=Date.parse("2026-10-03T08:00:00Z");
const env={GITHUB_TOKEN:"diagnostic-github-secret",TV_WEBHOOK_SECRET:"diagnostic-tv-secret",OTHER_API_KEY:"extra-sensitive-value"};
const candle=normalizeCandle({symbol:"BTCUSDT.P",timeframe:"1h",time,open:100,high:110,low:90,close:105,volume:1,oi:null,oi_status:"unavailable",oi_unavailable_reason:"fixture"},NOW);
for(const stage of ["history_get","daily_get","history_parse","daily_parse","daily_put","latest_get","latest_parse","latest_put"]){
 test("diagnostic failure location: "+stage,async()=>{
  const logs=[];const calls=[];
  const fetcher=async(url,options)=>{
   const path=new URL(url).pathname.split("/contents/")[1];calls.push({path,method:options.method});
   const location=path.endsWith("history.jsonl")?"history":path.startsWith("output/")?"latest":"daily";
   if(stage===location+"_"+options.method.toLowerCase())throw new DOMException("aborted "+Object.values(env).join(" ")+" Authorization: Bearer hidden-value","AbortError");
   if(stage===location+"_parse" && options.method==="GET")return Response.json({sha:"s",encoding:"base64",content:Buffer.from("malformed-json").toString("base64")});
   return options.method==="GET"?new Response("",{status:404}):Response.json({content:{sha:"s"}});
  };
  await saveBatch([candle],env,{fetcher,now:()=>NOW,requestStartedMs:NOW-2500,logger:{info:(...x)=>logs.push(x),error:(...x)=>logs.push(x)}});
  const failure=logs.find(x=>x[0]==="candle_save_failed")?.[1];assert.ok(failure);assert.equal(failure.stage,stage);assert.equal(failure.timeframe,"1h");assert.equal(failure.candle_time,candle.candle_time_utc);assert.equal(failure.retry_attempt,1);assert.equal(failure.request_elapsed_ms,2500);assert.ok(failure.path);
  const parsing=stage.endsWith("parse");assert.equal(failure.exception_name,parsing?"SyntaxError":"AbortError");assert.equal(failure.abort,!parsing);assert.equal(failure.timeout,!parsing);assert.equal(failure.method,parsing?null:stage.endsWith("put")?"PUT":"GET");
  for(const secret of [...Object.values(env),"hidden-value"])assert.ok(!JSON.stringify(logs).includes(secret));
 });
}
test("diagnostic retries remain three and expose final attempt",async()=>{
 let puts=0;const logs=[];
 await saveBatch([candle],env,{now:()=>NOW,fetcher:async(u,o)=>o.method==="GET"?new Response("",{status:404}):(puts++,new Response("",{status:409})),logger:{info(){},error:(...a)=>logs.push(a)}});
 const r=logs[0][1];assert.equal(puts,3);assert.equal(r.retry_attempt,3);assert.equal(r.stage,"daily_put");assert.equal(r.code,"github_retry_exhausted");
});
test("HTTP error code retained with stage and method",async()=>{
 const logs=[];await saveBatch([candle],env,{now:()=>NOW,fetcher:async()=>new Response("forbidden",{status:403}),logger:{info(){},error:(...a)=>logs.push(a)}});
 assert.equal(logs[0][1].code,"github_get_403");assert.equal(logs[0][1].stage,"history_get");assert.equal(logs[0][1].method,"GET");
});
