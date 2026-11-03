'use strict';
// Original MIT implementation. Templates come from the local bot.
const http = require('node:http');
const MAX_PIXELS = 20000000;
class HttpError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
function integer(value, fallback, low, high) {
  const n = value === undefined ? fallback : Number(value);
  if (!Number.isInteger(n) || n < low || n > high) throw new HttpError(400, `integer outside ${low}..${high}`);
  return n;
}
function readJson(req, limit, timeout = 5000) {
  return new Promise((resolve, reject) => {
    const chunks = []; let size = 0;
    const done = (error, value) => {
      clearTimeout(timer);
      req.removeListener('data', onData); req.removeListener('end', onEnd);
      req.removeListener('error', onError); req.removeListener('aborted', onAborted);
      error ? reject(error) : resolve(value);
    };
    const onError = error => done(error);
    const onAborted = () => done(new HttpError(400, 'request aborted'));
    const onData = chunk => {
      size += chunk.length;
      if (size > limit) { req.resume(); return done(new HttpError(413, 'body too large')); }
      chunks.push(chunk);
    };
    const onEnd = () => {
      try {
        const data = JSON.parse(Buffer.concat(chunks).toString('utf8'));
        if (!data || typeof data !== 'object' || Array.isArray(data)) throw Error();
        done(null, data);
      } catch { done(new HttpError(400, 'invalid JSON object')); }
    };
    const timer = setTimeout(() => { req.resume(); done(new HttpError(408, 'body timeout')); }, timeout);
    req.on('data', onData); req.on('end', onEnd);
    req.on('error', onError); req.on('aborted', onAborted);
  });
}
function sendJson(res, status, data) {
  if (res.destroyed || res.writableEnded) return;
  res.writeHead(status, {'Content-Type':'application/json; charset=utf-8', 'Cache-Control':'no-store'});
  res.end(JSON.stringify(data));
}
function createRendererServer({chromium, maxConcurrent=2, maxBody=2*1024*1024, token=''} = {}) {
  let browserPromise=null, liveBrowser=null, ready=false, closing=false;
  let active=0, renders=0, failures=0, lastMs=0;
  const started=Date.now(), sources=Object.create(null);
  const launch = async () => {
    if (!browserPromise) {
      browserPromise = (async () => {
        const engine = chromium || require('playwright').chromium;
        const browser = await engine.launch({headless:true, timeout:15000, args:['--no-sandbox']});
        liveBrowser=browser;
        browser.on('disconnected', () => {
          if (liveBrowser === browser) { liveBrowser=null; browserPromise=null; ready=false; }
        });
        const context = await browser.newContext({javaScriptEnabled:false});
        try {
          const page = await context.newPage();
          await page.setContent('<html><body>ready</body></html>', {timeout:5000});
          await page.screenshot({type:'png', timeout:5000});
        } finally { await context.close(); }
        ready=true;
        return browser;
      })().catch(async error => {
        const browser=liveBrowser;
        liveBrowser=null; browserPromise=null; ready=false;
        if (browser) await browser.close().catch(() => {});
        throw error;
      });
    }
    return browserPromise;
  };
  async function render(payload, mode, signal) {
    if (typeof payload.html !== 'string' || !payload.html.trim()) throw new HttpError(400,'html is required');
    const paginate=mode==='/render-pages', wholeCard=mode==='/render-card';
    const width=integer(payload.width,900,320,wholeCard?MAX_PIXELS/200:1600);
    const height=integer(payload.height,wholeCard?200:paginate?2400:1200,200,2400);
    if (width*height>MAX_PIXELS) throw new HttpError(400,'pixel budget exceeded');
    const timeout=integer(payload.timeout_ms,20000,1000,60000);
    let context, timer, expired=false;
    const deadline = new Promise((_, reject) => {
      timer=setTimeout(() => { expired=true; context?.close().catch(() => {}); reject(new HttpError(504,'render deadline exceeded')); },timeout);
    });
    const job = (async () => {
      const browser=await launch();
      if (expired || signal.aborted) throw new HttpError(504,'render cancelled');
      context=await browser.newContext({viewport:{width,height},deviceScaleFactor:1,javaScriptEnabled:false,serviceWorkers:'block'});
      const cancel=() => context.close().catch(() => {});
      signal.addEventListener('abort',cancel,{once:true});
      try {
        if (expired || signal.aborted) throw new HttpError(504,'render cancelled');
        await context.route('**/*',route => {
          const url=route.request().url();
          return url === 'about:blank' || url.startsWith('data:') ? route.continue() : route.abort();
        });
        const page=await context.newPage(); page.setDefaultTimeout(timeout);
        await page.setContent(payload.html,{waitUntil:'load',timeout});
        await page.evaluate(async () => {
          await document.fonts.ready;
          await Promise.all([...document.images].map(image => image.decode()));
        });
        if (wholeCard) {
          const result=await page.evaluate(({width,maxPixels}) => {
            const roots=document.querySelectorAll('[data-card]');
            if (roots.length!==1) return {error:'expected one data-card root'};
            const root=roots[0], bounds=root.getBoundingClientRect();
            const w=Math.ceil(bounds.width), h=Math.ceil(bounds.height);
            if (w<1 || h<1 || w*h>maxPixels) return {error:'pixel budget exceeded'};
            if (w>width || root.scrollWidth>w || root.scrollHeight>h)
              return {error:'card content overflows root'};
            return {};
          },{width,maxPixels:MAX_PIXELS});
          if (result.error) throw new HttpError(422,result.error);
          return [await page.locator('[data-card]').screenshot({type:'png',timeout})];
        }
        if (!paginate) {
          const size=await page.evaluate(() => ({width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight}));
          if (size.width>width || size.height>height) throw new HttpError(422,'card overflows viewport');
          return [await page.screenshot({type:'png',fullPage:false,timeout})];
        }
        const result=await page.evaluate(({width,height}) => {
          const root=document.querySelector('[data-card]');
          if (!root) return {error:'missing data-card root'};
          const blocks=[...root.children], parent=root.parentNode;
          const newPage=() => {
            const sheet=root.cloneNode(false); sheet.removeAttribute('id');
            sheet.style.width=width+'px'; sheet.style.minHeight='200px'; sheet.style.height='auto';
            sheet.dataset.renderPage='1'; parent.insertBefore(sheet,root); return sheet;
          };
          let current=newPage(); const pages=[current];
          for (const block of blocks) {
            current.appendChild(block);
            if (current.getBoundingClientRect().height>height) {
              current.removeChild(block);
              if (!current.children.length) return {error:'single block exceeds page height'};
              current=newPage(); pages.push(current); current.appendChild(block);
              if (current.getBoundingClientRect().height>height) return {error:'single block exceeds page height'};
            }
            if (pages.length>12) return {error:'too many pages'};
          }
          root.remove(); let pixels=0;
          for (const sheet of pages) {
            const bounds=sheet.getBoundingClientRect();
            if (sheet.scrollWidth>width || bounds.height>height) return {error:'page overflow'};
            pixels+=Math.ceil(bounds.width)*Math.ceil(bounds.height);
          }
          return pixels>20000000 ? {error:'pixel budget exceeded'} : {count:pages.length};
        },{width,height});
        if (result.error) throw new HttpError(422,result.error);
        const output=[];
        for (let i=0;i<result.count;i++) output.push(await page.locator('[data-render-page]').nth(i).screenshot({type:'png',timeout}));
        return output;
      } finally { signal.removeEventListener('abort',cancel); await context.close().catch(() => {}); }
    })();
    try { return await Promise.race([job,deadline]); }
    finally { clearTimeout(timer); if (context) await context.close().catch(() => {}); }
  }
  const server=http.createServer(async (req,res) => {
    const path=(req.url || '/').split('?')[0];
    if (req.method==='GET' && path==='/health') return sendJson(res,ready?200:503,{
      ok:ready,ready,service:'qqbot-playwright',backend:'playwright',deployment:process.env.RENDER_DEPLOYMENT || 'host',
      renders,failures,active,last_ms:lastMs,max_concurrent:maxConcurrent,sources,uptime_s:Math.floor((Date.now()-started)/1000)});
    if (!['/render','/render-pages','/render-card'].includes(path)) return sendJson(res,404,{error:'not found'});
    if (req.method!=='POST') return sendJson(res,405,{error:'method not allowed'});
    if (token && req.headers['x-render-token']!==token && req.headers.authorization!==`Bearer ${token}`)
      return sendJson(res,401,{error:'unauthorized'});
    // Reserve capacity before receiving any request body.
    if (closing || active>=maxConcurrent) return sendJson(res,429,{error:'renderer busy'});
    active++;
    const origin=String(req.headers['x-render-source'] || 'legacy').replace(/[^a-zA-Z0-9_.-]/g,'').slice(0,40) || 'legacy';
    const source=Object.hasOwn(sources,origin) || Object.keys(sources).length<32 ? origin : 'other';
    const metrics=sources[source] ||= {renders:0,failures:0};
    const controller=new AbortController();
    const cancel=() => { if (!res.writableEnded) controller.abort(); }; res.on('close',cancel);
    try {
      const body=await readJson(req,maxBody), begin=Date.now();
      const pages=await render(body,path,controller.signal);
      lastMs=Date.now()-begin; renders++; metrics.renders++;
      if (path==='/render-pages') sendJson(res,200,{pages:pages.map(png => png.toString('base64')),render_ms:lastMs});
      else if (!res.destroyed) {
        res.writeHead(200,{'Content-Type':'image/png','Content-Length':pages[0].length,'Cache-Control':'no-store','X-Render-Ms':String(lastMs)});
        res.end(pages[0]);
      }
    } catch (error) {
      failures++; metrics.failures++;
      const status=error instanceof HttpError ? error.status : 500;
      console.error(`[render] source=${source} status=${status} type=${error.name}`);
      sendJson(res,status,{error:error instanceof HttpError ? error.message : 'render failed'});
    } finally { active--; res.removeListener('close',cancel); }
  });
  server.requestTimeout=10000; server.headersTimeout=10000;
  const retry=setInterval(() => { if (!ready && !closing) launch().catch(() => {}); },5000); retry.unref();
  server.on('listening',() => launch().catch(() => {}));
  server.on('close',() => { closing=true; clearInterval(retry); liveBrowser?.close().catch(() => {}); });
  return server;
}
module.exports={createRendererServer,HttpError,readJson};
if (require.main===module) {
  const server=createRendererServer({maxConcurrent:integer(process.env.MAX_CONCURRENT,2,1,8),
    maxBody:integer(process.env.MAX_BODY_BYTES,2*1024*1024,1024,8*1024*1024),token:process.env.RENDER_TOKEN || ''});
  server.listen(Number(process.env.PORT || 3000),process.env.HOST || '0.0.0.0');
  for (const signal of ['SIGTERM','SIGINT']) process.on(signal,() => {
    server.close(() => process.exit(0)); setTimeout(() => process.exit(1),5000).unref();
  });
}
