import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {GitHubStore,normalizeCandle,saveBatch,saveLegacy15m,DURATIONS} from "../cloudflare/worker.mjs";
const NOW=Date.parse("2026-10-03T09:30:00Z");
const env={GITHUB_TOKEN:"binding-token",TV_WEBHOOK_SECRET:"binding-secret"};
function candle(tf){const time=Math.floor(NOW/DURATIONS[tf])*DURATIONS[tf]-DURATIONS[tf];return normalizeCandle({symbol:"BTCUSDT.P",timeframe:tf,time,open:100,high:110,low:90,close:105,volume:1,oi:10,...(tf==="15m"?{}:{oi_period_start_ms:time,oi_period_end_ms:time+DURATIONS[tf],oi_time:time+DURATIONS[tf],oi_time_basis:"confirmed_oi_bar_close_boundary"})},NOW);}
function runtimeFetch(){const calls=[],files=new Map();async function fetcher(url,options){
 // Workers host methods reject an unrelated object receiver. Bare calls and
 // calls with the global receiver are accepted; ordinary Node mocks missed this.
 const receiver=this;if(receiver!==globalThis && receiver!==undefined)throw new TypeError("Illegal invocation: function called with incorrect `this` reference.");
 const path=new URL(url).pathname.split("/contents/")[1];calls.push({path,method:options.method,receiver});
 if(options.method==="GET"){if(!files.has(path))return new Response("",{status:404});return Response.json({sha:"s",encoding:"base64",content:Buffer.from(files.get(path)).toString("base64")});}
 files.set(path,Buffer.from(JSON.parse(options.body).content,"base64").toString());return Response.json({content:{sha:"s"}});
}return {fetcher,calls,files};}
test("pre-fix actual API body reproduces three history_get Illegal invocations",async()=>{
 const source=readFileSync(new URL("../cloudflare/worker.mjs",import.meta.url),"utf8");
 assert.ok(source.includes("this.fetcher.call(globalThis,url,"));
 const oldBody=GitHubStore.prototype.api.toString().replace("async api(","async function api(").replace("this.fetcher.call(globalThis,url,","this.fetcher(url,");
 const previousApi=(0,eval)("("+oldBody+")");
 for(const tf of ["1h","4h","1d"]){const host=runtimeFetch();const store=new GitHubStore(env,host.fetcher,()=>NOW);store.api=previousApi;
 await assert.rejects(()=>store.append(candle(tf)),e=>e.name==="TypeError" && e.message==="Illegal invocation: function called with incorrect `this` reference.");
 assert.equal(store.diagnostic.stage,"history_get");assert.equal(store.diagnostic.path,`data_market/btc_anytime/${tf}/btc_${tf}_history.jsonl`);assert.equal(host.calls.length,0);
 }
});
for(const tf of ["1h","4h","1d"])test("fixed global host receiver: "+tf+" history GET and save succeed",async()=>{
 const host=runtimeFetch(),logs=[];await saveBatch([candle(tf)],env,{fetcher:host.fetcher,now:()=>NOW,logger:{info:(...x)=>logs.push(x),error:(...x)=>logs.push(x)}});
 assert.ok(host.calls.some(x=>x.path.endsWith(`btc_${tf}_history.jsonl`)));assert.ok(host.calls.every(x=>x.receiver===globalThis));assert.equal(host.files.size,2);assert.ok(!logs.some(x=>x[0]==="candle_save_failed"));assert.equal(logs.at(-1)[1].outcome,"inserted");
 for(const secret of Object.values(env))assert.ok(!JSON.stringify(logs).includes(secret));
});
test("default global fetch uses the global receiver for all HTFs",async()=>{
 const saved=globalThis.fetch,host=runtimeFetch();globalThis.fetch=host.fetcher;const logs=[];
 try{await saveBatch([candle("1h"),candle("4h"),candle("1d")],env,{now:()=>NOW,logger:{info:(...x)=>logs.push(x),error:(...x)=>logs.push(x)}});}finally{globalThis.fetch=saved;}
 assert.equal(host.files.size,6);assert.ok(host.calls.every(x=>x.receiver===globalThis));assert.equal(logs.filter(x=>x[0]==="candle_save").length,3);
});
test("15m bare fetch succeeds under the same host receiver restriction",async()=>{
 const host=runtimeFetch();await saveLegacy15m(candle("15m"),env,{fetcher:host.fetcher,logger:{log(){}}});assert.equal(host.files.size,2);assert.ok(host.calls.every(x=>x.receiver===undefined));
});
