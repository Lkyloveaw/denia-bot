'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const http=require('node:http');
const {EventEmitter,once}=require('node:events');
const {createRendererServer}=require('./server');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));

function engine() {
  let launches=0, current;
  return {get launches(){return launches;},get browser(){return current;},
    async launch(){
      launches++; current=new EventEmitter();
      current.newContext=async()=>({
        async newPage(){return {async setContent(){},async screenshot(){return Buffer.from('png');}};},
        async close(){}
      });
      current.close=async()=>current.emit('disconnected');
      return current;
    }};
}
async function start(t,options={}) {
  const server=createRendererServer({chromium:engine(),...options});
  server.listen(0,'127.0.0.1'); await once(server,'listening');
  t.after(()=>{server.closeAllConnections();server.close();});
  return {server,url:`http://127.0.0.1:${server.address().port}`};
}
test('health requires a real browser and recovers after disconnect',async t=>{
  const chromium=engine(); const {url}=await start(t,{chromium});
  await pause(10);
  assert.equal((await (await fetch(url+'/health')).json()).ready,true);
  chromium.browser.emit('disconnected');
  assert.equal((await fetch(url+'/health')).status,503);
  assert.equal(chromium.launches,1);
});
test('unlaunchable browser is not healthy',async t=>{
  const {url}=await start(t,{chromium:{async launch(){throw Error('missing');}}});
  await pause(10);
  assert.equal((await fetch(url+'/health')).status,503);
});
test('body readers reserve slots before rendering and release on disconnect',async t=>{
  const {url}=await start(t);
  const readers=[];
  for(let i=0;i<2;i++) {
    const req=http.request(url+'/render',{method:'POST',headers:{'Content-Type':'application/json'}});
    req.on('error',()=>{}); req.write('{"html":'); readers.push(req);
  }
  t.after(()=>readers.forEach(req=>req.destroy()));
  for(let i=0;i<50;i++) {
    if ((await (await fetch(url+'/health')).json()).active===2) break;
    await pause(5);
  }
  const busy=await fetch(url+'/render',{method:'POST',body:'{}'});
  assert.equal(busy.status,429);
  assert.equal((await (await fetch(url+'/health')).json()).active,2);
  readers.forEach(req=>req.destroy());
  await pause(50);
  assert.equal((await (await fetch(url+'/health')).json()).active,0);
});
test('oversized and invalid JSON release their reserved capacity',async t=>{
  const {url}=await start(t,{maxBody:30});
  for(const [body,status] of [['x'.repeat(60),413],['{',400],['[]',400]]) {
    const response=await fetch(url+'/render',{method:'POST',body});
    assert.equal(response.status,status);
    assert.equal((await (await fetch(url+'/health')).json()).active,0);
  }
});
test('unsupported dimensions are rejected, never silently clipped',async t=>{
  const {url}=await start(t);
  const response=await fetch(url+'/render',{method:'POST',body:JSON.stringify({html:'ok',width:2880})});
  assert.equal(response.status,400);
});

test('complete cards allow wide and tall content with a pixel budget',
  {skip:process.env.RENDER_TEST_REAL!=='1'},async t=>{
    const {url}=await start(t,{chromium:require('playwright').chromium});
    const post=(path,html,width=3000)=>fetch(url+path,{method:'POST',
      body:JSON.stringify({html,width})});
    const html='<style>body{margin:0}main{width:3000px;height:2800px;background:#f4f2ec}</style><main data-card>完整帮助</main>';
    const response=await post('/render-card',html);
    assert.equal(response.status,200);
    assert.equal(response.headers.get('content-type'),'image/png');
    const png=Buffer.from(await response.arrayBuffer());
    assert.equal(png.readUInt32BE(16),3000);
    assert.equal(png.readUInt32BE(20),2800);
    assert.equal((await post('/render',html)).status,400);
    assert.equal((await post('/render-pages',html)).status,400);
    for(const bad of [
      '<main>missing root</main>',
      '<main data-card></main><main data-card></main>',
      '<main data-card style="width:3000px;height:7000px">too large</main>',
      '<main data-card style="width:3000px;height:200px"><div style="width:3100px">overflow</div></main>',
      '<main data-card style="width:3000px;height:200px"><div style="height:300px">overflow</div></main>',
    ]) assert.equal((await post('/render-card',bad)).status,422);
    assert.equal((await (await fetch(url+'/health')).json()).active,0);
  });
