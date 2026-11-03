import asyncio, sys
sys.path.insert(0, '/opt/qqbot/nonebot'); sys.path.insert(0, '/opt/qqbot/nonebot/parser_deps')
import httpx
async def main():
    async with httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=True,
                                 headers={'User-Agent':'Mozilla/5.0'}) as c:
        r = await c.get('https://api.fxtwitter.com/thsottiaux/status/2116405414921384099')
        print('HTTP', r.status_code)
        d = r.json()
        print('code:', d.get('code'), '| message:', d.get('message'))
        t = d.get('tweet') or {}
        if t:
            print('作者:', (t.get('author') or {}).get('name'))
            print('正文:', (t.get('text') or '')[:200])
            print('media:', t.get('media'))
            print('has media keys:', list((t.get('media') or {}).keys()))
asyncio.run(main())
