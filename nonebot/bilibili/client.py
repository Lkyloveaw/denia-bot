"""Bounded API reads; optional parser cookies authenticate source reads only."""
import asyncio
import hashlib
import json
import re
import time
from urllib.parse import urlencode

import httpx

from .model import BiliError, Item, Page, clean, face_url, identifier, image_url
from .auth import cookie_header

SPACE = '/x/polymer/web-dynamic/v1/feed/space'
CARD = '/x/web-interface/card'
SPI = '/x/frontend/finger/spi'
NAV = '/x/web-interface/nav'
SEARCH = '/x/web-interface/wbi/search/type'
PATHS = {SPACE, CARD}
_BROWSER_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
               'AppleWebKit/537.36 (KHTML, like Gecko) '
               'Chrome/131.0.0.0 Safari/537.36')
_JSON_HEADERS = {
    'User-Agent': _BROWSER_UA,
    'Referer': 'https://www.bilibili.com/',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Encoding': 'identity',
}
_WBI_MIXIN_TAB = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)


def mapping(value):
    if not isinstance(value,dict):
        raise BiliError('REMOTE_SCHEMA')
    return value


def wbi_mixin_key(img_url, sub_url):
    def key(value):
        if not isinstance(value, str):
            raise BiliError('REMOTE_SCHEMA')
        value = value.rsplit('/', 1)[-1].split('.')[0]
        if not value or len(value) > 128:
            raise BiliError('REMOTE_SCHEMA')
        return value
    raw = key(img_url) + key(sub_url)
    if len(raw) < max(_WBI_MIXIN_TAB) + 1:
        raise BiliError('REMOTE_SCHEMA')
    return ''.join(raw[index] for index in _WBI_MIXIN_TAB)[:32]


def wbi_sign(params, mixin_key, *, now=None):
    if not isinstance(mixin_key, str) or len(mixin_key) != 32:
        raise BiliError('REMOTE_SCHEMA')
    values = {}
    for key, value in params.items():
        text = str(value)
        values[str(key)] = ''.join(char for char in text if char not in "!'()*")
    values['wts'] = str(int(time.time() if now is None else now))
    query = urlencode(sorted(values.items()))
    values['w_rid'] = hashlib.md5((query + mixin_key).encode('utf-8')).hexdigest()
    return values


def normalize_item(raw, uid, depth=0):
    raw = mapping(raw)
    if depth>1:
        raise BiliError('REMOTE_SCHEMA')
    if raw.get('visible') is not True:
        raise BiliError('PUBLIC_UNPROVEN')
    modules = mapping(raw.get('modules'))
    author = mapping(modules.get('module_author'))
    if isinstance(author.get('mid'),bool) or str(author.get('mid')) != uid:
        raise BiliError('REMOTE_SCHEMA')
    dynamic = mapping(modules.get('module_dynamic'))
    major = mapping(dynamic.get('major') or {})
    if major.get('type') in {'MAJOR_TYPE_BLOCKED','MAJOR_TYPE_NONE'}:
        raise BiliError('PUBLIC_UNPROVEN')
    kind = raw.get('type')
    if not isinstance(kind,str):
        raise BiliError('REMOTE_SCHEMA')
    desc = mapping(dynamic.get('desc') or {}).get('text','')
    text = clean(desc)
    labels = {'DYNAMIC_TYPE_WORD':'文字动态','DYNAMIC_TYPE_DRAW':'图文动态',
        'DYNAMIC_TYPE_AV':'视频动态','DYNAMIC_TYPE_ARTICLE':'专栏动态','DYNAMIC_TYPE_FORWARD':'转发动态'}
    label = labels.get(kind,'未知类型动态')
    major_type = major.get('type')
    title = ''
    cover = ''
    if major_type in {'MAJOR_TYPE_ARCHIVE','MAJOR_TYPE_ARTICLE','MAJOR_TYPE_OPUS'}:
        key = {'MAJOR_TYPE_ARCHIVE':'archive','MAJOR_TYPE_ARTICLE':'article','MAJOR_TYPE_OPUS':'opus'}[major_type]
        content = mapping(major.get(key))
        content_title = clean(content.get('title') or '',200)
        if key == 'archive':
            title = content_title
            cover = image_url(content.get('cover'))
        summary = mapping(content.get('summary') or {}).get('text','') if key=='opus' else content.get('desc','')
        extra = '\n'.join(p for p in (content_title,clean(summary)) if p)
        if extra: text = (text+'\n'+extra).strip()
        if key=='opus': label='图文动态'
    elif major_type == 'MAJOR_TYPE_DRAW':
        images = mapping(major.get('draw')).get('items')
        if not isinstance(images,list) or len(images)>100:
            raise BiliError('REMOTE_SCHEMA')
        text += '\n附 '+str(len(images))+' 张图片（打开原动态查看）'
    if kind == 'DYNAMIC_TYPE_FORWARD':
        original = raw.get('orig')
        if original is None:
            text += '\n原文状态未确认'
        elif mapping(original).get('type') == 'DYNAMIC_TYPE_NONE':
            text += '\n原文不可用'
        else:
            original_uid = str(mapping(mapping(original.get('modules')).get('module_author')).get('mid'))
            identifier(original_uid)
            value = normalize_item(original,original_uid,depth+1)
            text += '\n转发 @'+value.name+'：\n'+value.text
    if label=='未知类型动态':
        text = '未知类型，请打开原动态查看。'
    try:
        ts = author.get('pub_ts')
        if isinstance(ts,bool): raise ValueError
        ts = float(ts)
    except (TypeError,ValueError,OverflowError) as exc:
        raise BiliError('REMOTE_SCHEMA') from exc
    return Item(identifier(raw.get('id_str')),uid,clean(author.get('name'),80),ts,label,clean(text),
                face_url(author.get('face')),title,cover)


class BiliClient:
    def __init__(self, *, transport=None, cookie_provider=None):
        self.http = httpx.AsyncClient(base_url='https://api.bilibili.com',
            headers={'User-Agent':'denia-bot-bilibili/0.3','Referer':'https://space.bilibili.com/',
                'Accept-Encoding':'identity'},
            timeout=httpx.Timeout(10), limits=httpx.Limits(max_connections=2,max_keepalive_connections=2),
            follow_redirects=False, trust_env=False, transport=transport)
        self.restricted = False
        self.cookie_provider = cookie_provider
        self.authentication_failed = False
        self._auth_identity = None
        self._auth_rejected = None
        self._search_cache = None

    def _login_headers(self):
        try:
            header = cookie_header(self.cookie_provider())
        except Exception:
            self.authentication_failed = True
            raise BiliError('REMOTE_AUTH_REQUIRED') from None
        identity = hashlib.sha256(header.encode('ascii')).digest()
        if identity == self._auth_rejected:
            self.authentication_failed = True
            raise BiliError('REMOTE_AUTH_REQUIRED')
        self._auth_identity = identity
        return {'Cookie': header}

    def require_delivery(self):
        # Cookie presence is not a delivery veto. Keep failed/missing login
        # from releasing pending items until a source read succeeds again.
        if self.restricted:
            raise BiliError('REMOTE_RESTRICTED')
        if self.cookie_provider is not None:
            self._login_headers()
            if self.authentication_failed:
                raise BiliError('REMOTE_AUTH_REQUIRED')

    async def __aenter__(self): return self
    async def __aexit__(self,*args): await self.close()
    async def close(self): await self.http.aclose()

    async def _request_json(self,path,params,*,headers=None,cookies=None,
                            allowed_codes=(0,),authenticated=False):
        if path not in {SPACE,CARD,SPI,NAV,SEARCH}:
            raise BiliError('INVALID_ARGUMENT')
        if self.restricted:
            raise BiliError('REMOTE_RESTRICTED')
        self.http.cookies.clear()
        try:
            async with self.http.stream('GET',path,params=params,
                                        headers=headers or {},cookies=cookies or {}) as response:
                if response.status_code in {403,412,429}:
                    self.restricted = True
                    raise BiliError('REMOTE_RESTRICTED')
                if response.status_code == 401 and authenticated:
                    self.authentication_failed = True
                    self._auth_rejected = self._auth_identity
                    raise BiliError('REMOTE_AUTH_REQUIRED')
                if response.status_code != 200:
                    raise BiliError('REMOTE_HTTP')
                if response.headers.get('content-encoding','identity').lower() not in {'','identity'}:
                    raise BiliError('REMOTE_SCHEMA')
                content = bytearray()
                # Identity encoding was checked above. Iterate the raw stream:
                # aiter_bytes() closes at EOF before we can retain refusal state.
                # Only a complete, bounded body may mark either refusal.
                async for part in response.stream:
                    content.extend(part)
                    if len(content)>2*1024*1024:
                        raise BiliError('REMOTE_SCHEMA')
                body = mapping(json.loads(content))
                code = body.get('code')
                if type(code) is not int: raise BiliError('REMOTE_SCHEMA')
                if code in {-352,-412,-509}:
                    self.restricted=True
                    raise BiliError('REMOTE_RESTRICTED')
                if authenticated and code in {-101,-111}:
                    self.authentication_failed = True
                    self._auth_rejected = self._auth_identity
                    raise BiliError('REMOTE_AUTH_REQUIRED')
                if code in {-101,-111} and code not in allowed_codes:
                    raise BiliError('REMOTE_AUTH_REQUIRED')
                if code not in allowed_codes:
                    raise BiliError('REMOTE_HTTP')
                if authenticated:
                    self.authentication_failed = False
                return mapping(body.get('data'))
        except httpx.TimeoutException as exc:
            raise BiliError('DEADLINE') from exc
        except httpx.HTTPError as exc:
            raise BiliError('REMOTE_HTTP') from exc
        except (json.JSONDecodeError,UnicodeError,ValueError,TypeError) as exc:
            raise BiliError('REMOTE_SCHEMA') from exc
        finally:
            # Server Set-Cookie never becomes an alternate credential source,
            # nor leaks into a subsequent anonymous search or another account.
            self.http.cookies.clear()

    async def _read(self,path,params):
        if path not in PATHS:
            raise BiliError('INVALID_ARGUMENT')
        if self.restricted:
            raise BiliError('REMOTE_RESTRICTED')
        authenticated = self.cookie_provider is not None
        headers = self._login_headers() if authenticated else None
        return await self._request_json(path,params,headers=headers,authenticated=authenticated)

    async def _get(self,path,params):
        try:
            return await asyncio.wait_for(self._read(path,params),15)
        except asyncio.TimeoutError as exc:
            raise BiliError('DEADLINE') from exc

    async def _get_json(self,path,params,**kwargs):
        try:
            return await asyncio.wait_for(self._request_json(path,params,**kwargs),15)
        except asyncio.TimeoutError as exc:
            raise BiliError('DEADLINE') from exc

    async def _search_context(self):
        now=time.monotonic()
        if self._search_cache is not None and self._search_cache[0]>now:
            return self._search_cache[1],self._search_cache[2]
        spi=await self._get_json(SPI,{},headers=_JSON_HEADERS)
        cookies={}
        for name,value in (('buvid3',spi.get('b_3')),('buvid4',spi.get('b_4'))):
            if (not isinstance(value,str) or not value or len(value)>512 or
                    any(ord(char)<0x21 or ord(char)>0x7e or char in ';,"\\' for char in value)):
                raise BiliError('REMOTE_SCHEMA')
            cookies[name]=value
        nav=await self._get_json(NAV,{},headers=_JSON_HEADERS,cookies=cookies,
                                 allowed_codes=(0,-101))
        wbi=mapping(nav.get('wbi_img'))
        key=wbi_mixin_key(wbi.get('img_url'),wbi.get('sub_url'))
        self._search_cache=(now+3600,cookies,key)
        return cookies,key

    async def avatar(self,item):
        """One bounded read from the pinned avatar CDN; no credential, no redirects."""
        try:
            return await asyncio.wait_for(self._image(item.face, 2*1024*1024),5)
        except asyncio.TimeoutError as exc:
            raise BiliError('DEADLINE') from exc

    async def cover(self,item):
        """One bounded read from the pinned video-cover CDN; optional decoration."""
        try:
            return await asyncio.wait_for(self._image(item.cover, 6*1024*1024),5)
        except asyncio.TimeoutError as exc:
            raise BiliError('DEADLINE') from exc

    async def _image(self,url,max_bytes):
        if not url:
            return None
        # The CDN never sees the login cookie; the host allowlist lives in image_url/face_url.
        self.http.cookies.clear()
        try:
            async with self.http.stream('GET',url) as response:
                if response.status_code != 200:
                    raise BiliError('REMOTE_HTTP')
                if (response.headers.get('content-encoding','identity').lower() not in {'','identity'}):
                    raise BiliError('REMOTE_SCHEMA')
                kind = (response.headers.get('content-type') or '').split(';')[0].strip().lower()
                if kind not in {'image/jpeg','image/png','image/webp','image/gif'}:
                    raise BiliError('REMOTE_SCHEMA')
                content = bytearray()
                async for part in response.stream:
                    content.extend(part)
                    if len(content) > max_bytes:
                        raise BiliError('REMOTE_SCHEMA')
            return bytes(content) or None
        except httpx.HTTPError as exc:
            raise BiliError('REMOTE_HTTP') from exc
        finally:
            self.http.cookies.clear()

    async def validate_user(self,uid):
        identifier(uid)
        data = await self._get(CARD,{'mid':uid})
        card = mapping(data.get('card'))
        if isinstance(card.get('mid'),bool) or str(card.get('mid'))!=uid:
            raise BiliError('REMOTE_SCHEMA')
        return clean(card.get('name'),80)

    async def _page_data(self,uid,cursor=None):
        identifier(uid)
        if cursor is not None and (not isinstance(cursor,str) or not cursor or len(cursor)>1024):
            raise BiliError('REMOTE_SCHEMA')
        params = {'host_mid':uid,'features':'itemOpusStyle'}
        if cursor is not None: params['offset']=cursor
        data = await self._get(SPACE,params)
        items = data.get('items')
        if not isinstance(items,list) or len(items)>50:
            raise BiliError('REMOTE_SCHEMA')
        more = data.get('has_more')
        if type(more) is not bool:
            raise BiliError('REMOTE_SCHEMA')
        cursor = data.get('offset') if more else None
        if more and (not isinstance(cursor,str) or not cursor or len(cursor)>1024):
            raise BiliError('REMOTE_SCHEMA')
        return items,cursor,more

    async def diagnose_page(self,uid):
        """Exactly one head-page read; only non-content metadata may be reported."""
        items,cursor,more = await self._page_data(uid)
        return {'login_cookie_sent':self.cookie_provider is not None,'item_count':len(items),
            'has_more':more,'public_verified':False,'delivery_allowed':False}

    async def fetch_page(self,uid,cursor=None):
        items,cursor,more = await self._page_data(uid,cursor)
        return Page(tuple(normalize_item(value,uid) for value in items),cursor)

    async def search(self,keyword):
        if not isinstance(keyword,str) or not 1<=len(keyword)<=100:
            raise BiliError('INVALID_ARGUMENT')
        cookies,key = await self._search_context()
        params = wbi_sign({'search_type':'video','keyword':keyword,'page':1,'page_size':5},key)
        headers = {**_JSON_HEADERS,'Referer':'https://search.bilibili.com/'}
        data = await self._get_json(SEARCH,params,headers=headers,cookies=cookies)
        values = data.get('result',[])
        if not isinstance(values,list) or len(values)>100:
            raise BiliError('REMOTE_SCHEMA')
        result = []
        for raw in values[:5]:
            value = mapping(raw)
            if value.get('is_pay') or value.get('is_charge'):
                continue
            bvid = value.get('bvid')
            if not isinstance(bvid,str) or not re.fullmatch(r'BV[0-9A-Za-z]{10}',bvid):
                raise BiliError('REMOTE_SCHEMA')
            # Search remains anonymous; only WBI/buvid cookies are sent.
            result.append(clean(value.get('title'),200)+'\nUP：'+clean(value.get('author'),80)+
                '\n'+clean(value.get('description',''),500)+'\nhttps://www.bilibili.com/video/'+bvid)
        return tuple(result)
