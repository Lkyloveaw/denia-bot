"""Capture local preview pages at their exact CSS viewport dimensions."""
import asyncio
import base64
import json
import subprocess
import time
from pathlib import Path

import httpx
from websockets.asyncio.client import connect

from build_preview import CHROME, HERE, ROOT, VARIANTS, build

async def main():
    build()
    profile = ROOT / '.tmp' / 'statuscard-background-chrome' / 'cdp'
    profile.mkdir(parents=True, exist_ok=True)
    active = profile / 'DevToolsActivePort'
    if active.exists():
        active.unlink()
    proc = subprocess.Popen([str(CHROME), '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run', '--no-default-browser-check', '--disable-background-networking', '--disable-extensions', '--remote-debugging-port=0', f'--user-data-dir={profile}', '--window-size=1170,1810', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    metrics=[]
    try:
        for _ in range(100):
            if active.exists():
                break
            await asyncio.sleep(.1)
        port=active.read_text().splitlines()[0]
        async with httpx.AsyncClient(trust_env=False) as client:
            pages=(await client.get(f'http://127.0.0.1:{port}/json/list')).json()
        endpoint=next(p['webSocketDebuggerUrl'] for p in pages if p['type']=='page')
        async with connect(endpoint, max_size=32*1024*1024) as ws:
            request_id=0
            async def call(method, params=None):
                nonlocal request_id
                request_id+=1
                await ws.send(json.dumps({'id':request_id,'method':method,'params':params or {}}))
                while True:
                    result=json.loads(await asyncio.wait_for(ws.recv(),30))
                    if result.get('id')==request_id:
                        if 'error' in result:
                            raise RuntimeError(result['error'])
                        return result.get('result',{})
            await call('Page.enable')
            before=await call('Runtime.evaluate',{'expression':'JSON.stringify({innerWidth,innerHeight,outerWidth,outerHeight})','returnByValue':True})
            print('Initial browser viewport:', before['result']['value'], flush=True)
            targets=[(k,(1170,1810)) for k in VARIANTS]+[('comparison',(1720,870)),('sources',(1720,1130))]
            for name,(width,height) in targets:
                await call('Emulation.setDeviceMetricsOverride',{'width':width,'height':height,'deviceScaleFactor':1,'mobile':False})
                await call('Page.navigate',{'url':(HERE/f'{name}.html').as_uri()})
                for _ in range(100):
                    ready=await call('Runtime.evaluate',{'expression':"document.readyState==='complete' && [...document.images].every(i=>i.complete&&i.naturalWidth>0)",'returnByValue':True})
                    if ready['result'].get('value'):
                        break
                    await asyncio.sleep(.1)
                else:
                    raise RuntimeError(f'Images not loaded: {name}')
                await call('Runtime.evaluate',{'expression':'document.fonts.ready.then(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))))','awaitPromise':True})
                layout=await call('Runtime.evaluate',{'expression':'''JSON.stringify({width:innerWidth,height:innerHeight,images:[...document.images].map(i=>({ok:i.complete&&i.naturalWidth>0,width:i.naturalWidth,height:i.naturalHeight})),cards:[...document.querySelectorAll('.card')].map(c=>{let a=c.getBoundingClientRect(),b=c.querySelector('.chips').getBoundingClientRect(),f=c.querySelector('.footer').getBoundingClientRect();return {contentFits:b.bottom<f.top,footerFits:f.bottom<a.bottom}})})''','returnByValue':True})
                checks=json.loads(layout['result']['value'])
                assert checks['width']==width and checks['height']==height, checks
                assert all(i['ok'] for i in checks['images']),checks
                assert all(c['contentFits'] and c['footerFits'] for c in checks['cards']),checks
                shot=await call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':True,'clip':{'x':0,'y':0,'width':width,'height':height,'scale':1}})
                target=HERE/f'{name}.png'
                target.write_bytes(base64.b64decode(shot['data']))
                item={'file':target.name,'bytes':target.stat().st_size,'checks':checks}
                metrics.append(item)
                print(json.dumps(item),flush=True)
            await call('Browser.close')
    finally:
        if proc.poll() is None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.terminate()
    (HERE/'render-metrics.json').write_text(json.dumps(metrics,indent=2),encoding='utf-8')

if __name__=='__main__':
    asyncio.run(main())
