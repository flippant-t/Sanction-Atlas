// Exercises the watchlist and alerts Functions against a fake KV and fake key store.
import fs from 'fs';
const load = async p => {
  const src = fs.readFileSync(p,'utf8')
    .replace(/^import\s+\{([^}]*)\}\s+from\s+"([^"]*)";?$/gm,(m,names,from)=>`const {${names}} = __imp(${JSON.stringify(from)});`)
    .replace(/^export\s+(async\s+function|function|const)\b/gm,'$1');
  const mod = {exports:{}};
  const fn = new Function('module','__imp','crypto','URL','Response', src + '\n;module.exports={onRequestPost:typeof onRequestPost!=="undefined"?onRequestPost:null,onRequestGet:typeof onRequestGet!=="undefined"?onRequestGet:null,onRequestDelete:typeof onRequestDelete!=="undefined"?onRequestDelete:null};');
  fn(mod, () => LIB, crypto, URL, Response);
  return mod.exports;
};
const LIB = {
  json:(o,s=200,x={})=>new Response(JSON.stringify(o),{status:s,headers:{'content-type':'application/json',...x}}),
  guard:()=>null, preflight:()=>new Response(null,{status:204}),
  tierOf: async (req)=>{const k=new URL(req.url).searchParams.get('key')||req.headers.get('x-api-key')||'';
    if(!k) return {tier:'free',key:null};
    if(k==='ss_bad') return {tier:'free',key:null,invalid:true};
    if(k==='ss_free') return {tier:'free',key:k};
    return {tier:'pro',key:k};},
};
const KV = new Map();
const env = {KEYS:{
  get:async(k,o)=>{const v=KV.get(k); return v===undefined?null:(o?.type==='json'?JSON.parse(v):v);},
  put:async(k,v)=>{KV.set(k,v);}, delete:async k=>{KV.delete(k);},
  list:async({prefix})=>({keys:[...KV.keys()].filter(k=>k.startsWith(prefix)).map(name=>({name}))}),
}};
const req=(url,opt={})=>new Request('https://sanctionscope.com'+url,opt);
const post=(url,body,opt={})=>req(url,{method:'POST',body:JSON.stringify(body),headers:{'content-type':'application/json'},...opt});
let fails=0;
const check=async(label,res,want,pick)=>{const b=typeof res?.json==='function'?await res.json():res;const got=pick?pick(b,res):res.status;
  const ok=JSON.stringify(got)===JSON.stringify(want);
  console.log((ok?'PASS ':'FAIL ')+label, ok?'':`got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
  if(!ok)fails++; return b;};

const WL = await load('functions/api/v1/watchlist.js');
const ONE = await load('functions/api/v1/watchlist/[id].js');
const AL = await load('functions/api/v1/alerts.js');

await check('free key is refused', await WL.onRequestPost({request:post('/api/v1/watchlist?key=ss_free',{names:['Acme']}),env}), 403);
await check('bad key is 401', await WL.onRequestPost({request:post('/api/v1/watchlist?key=ss_bad',{names:['Acme']}),env}), 401);
await check('no names is 400', await WL.onRequestPost({request:post('/api/v1/watchlist?key=ss_k1',{names:[]}),env}), 400);
await check('http webhook refused', await WL.onRequestPost({request:post('/api/v1/watchlist?key=ss_k1',{names:['Acme'],webhook:'http://x.com/h'}),env}), 400);
await check('internal webhook refused', await WL.onRequestPost({request:post('/api/v1/watchlist?key=ss_k1',{names:['Acme'],webhook:'https://169.254.169.254/latest'}),env}), 400);
await check('localhost webhook refused', await WL.onRequestPost({request:post('/api/v1/watchlist?key=ss_k1',{names:['Acme'],webhook:'https://localhost/h'}),env}), 400);
await check('bad email refused', await WL.onRequestPost({request:post('/api/v1/watchlist?key=ss_k1',{names:['Acme'],email:'nope'}),env}), 400);
await check('5001 names refused', await WL.onRequestPost({request:post('/api/v1/watchlist?key=ss_k1',{names:Array.from({length:5001},(_,i)=>'Name '+i)}),env}), 413);

const created = await check('create returns 201', await WL.onRequestPost({request:post('/api/v1/watchlist?key=ss_k1',
  {label:'Q3 onboarding',names:['Sberbank of Russia','Acme Trading FZE','Sberbank of Russia','x'],email:'ops@c.com',webhook:'https://c.com/h'}),env}), 201);
await check('dupes and short names dropped', {ok:1}, {ok:1}, ()=>({ok:created.names===2?1:0}));
await check('webhook secret issued', {ok:1}, {ok:1}, ()=>({ok:/^whsec_[0-9a-f]{32}$/.test(created.webhook_secret)?1:0}));
await check('baseline is queued not computed', {ok:1}, {ok:1}, ()=>({ok:created.baseline==='queued'?1:0}));

const id = created.id;
await check('list shows one', await WL.onRequestGet({request:req('/api/v1/watchlist?key=ss_k1'),env}), 1, b=>b.count);
await check('detail returns names', await ONE.onRequestGet({request:req(`/api/v1/watchlist/${id}?key=ss_k1`),env,params:{id}}), 2, b=>b.names.length);
await check('another key cannot read it', await ONE.onRequestGet({request:req(`/api/v1/watchlist/${id}?key=ss_k9`),env,params:{id}}), 404);
await check('bad id shape is 404', await ONE.onRequestGet({request:req('/api/v1/watchlist/../../etc?key=ss_k1'),env,params:{id:'../../etc'}}), 404);

// replacing resets the baseline state
KV.set(`wlstate:ss_k1:${id}`, JSON.stringify({matches:{'Sberbank of Russia':['ofa:2']},baseline_done:true}));
await check('replace keeps the id', await WL.onRequestPost({request:post('/api/v1/watchlist?key=ss_k1',{id,names:['Acme Trading FZE'],label:'Q4'}),env}), 200);
await check('replace clears stale state', {ok:1}, {ok:1}, ()=>({ok:KV.has(`wlstate:ss_k1:${id}`)?0:1}));

// list cap
for(let i=0;i<9;i++) await WL.onRequestPost({request:post('/api/v1/watchlist?key=ss_k1',{names:['Filler '+i]}),env});
await check('11th list refused', await WL.onRequestPost({request:post('/api/v1/watchlist?key=ss_k1',{names:['One more']}),env}), 409);

// alerts
KV.set('alerts:ss_k1', JSON.stringify({generated:'2026-09-20',alerts:[
  {id:'al_1',date:'2026-09-20',type:'added',list_id:id,watched_name:'Acme Trading FZE'},
  {id:'al_2',date:'2026-09-12',type:'baseline',list_id:id},
  {id:'al_3',date:'2026-09-19',type:'removed',list_id:'wl_other'}]}));
await check('alerts returns all', await AL.onRequestGet({request:req('/api/v1/alerts?key=ss_k1'),env}), 3, b=>b.count);
await check('filter by type', await AL.onRequestGet({request:req('/api/v1/alerts?key=ss_k1&type=added'),env}), 1, b=>b.count);
await check('filter by since', await AL.onRequestGet({request:req('/api/v1/alerts?key=ss_k1&since=2026-09-19'),env}), 2, b=>b.count);
await check('filter by list', await AL.onRequestGet({request:req(`/api/v1/alerts?key=ss_k1&list_id=${id}`),env}), 2, b=>b.count);
await check('bad since is 400', await AL.onRequestGet({request:req('/api/v1/alerts?key=ss_k1&since=yesterday'),env}), 400);
await check('alerts need pro', await AL.onRequestGet({request:req('/api/v1/alerts?key=ss_free'),env}), 403);
await check('other key sees no alerts', await AL.onRequestGet({request:req('/api/v1/alerts?key=ss_k9'),env}), 0, b=>b.count);

// delete removes names and state
KV.set(`wlstate:ss_k1:${id}`, JSON.stringify({matches:{}}));
await check('delete works', await ONE.onRequestDelete({request:req(`/api/v1/watchlist/${id}?key=ss_k1`,{method:'DELETE'}),env,params:{id}}), 200);
await check('names gone', {ok:1}, {ok:1}, ()=>({ok:KV.has(`wl:ss_k1:${id}`)?0:1}));
await check('state gone too', {ok:1}, {ok:1}, ()=>({ok:KV.has(`wlstate:ss_k1:${id}`)?0:1}));
await check('delete again is 404', await ONE.onRequestDelete({request:req(`/api/v1/watchlist/${id}?key=ss_k1`,{method:'DELETE'}),env,params:{id}}), 404);

console.log(fails? `\n${fails} FAILED` : '\nall endpoint tests passed');
process.exit(fails?1:0);
