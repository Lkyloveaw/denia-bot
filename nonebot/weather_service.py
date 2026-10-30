"""QWeather access and persistent, opt-in push configuration."""
import re
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime
import httpx

PATH = Path(__file__).resolve().parent / 'data' / 'weather.sqlite3'

@contextmanager
def db(path=PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=5)
    os.chmod(path, 0o600)
    c.row_factory = sqlite3.Row
    c.executescript('CREATE TABLE IF NOT EXISTS config (name TEXT PRIMARY KEY, value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS pushes (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL, city TEXT NOT NULL, clock TEXT NOT NULL, last_date TEXT NOT NULL DEFAULT "");')
    try:
        with c:
            yield c
    finally:
        c.close()

def validate_host(host):
    host = host.strip().lower().removeprefix('https://').rstrip('/')
    if not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)*\.qweatherapi\.com', host):
        raise ValueError('API Host 格式不正确')
    return host

def config(path=PATH):
    with db(path) as c:
        return {r['name']:r['value'] for r in c.execute('SELECT name,value FROM config')}

def save_config(host, key, path=PATH):
    host = validate_host(host)
    with db(path) as c:
        c.execute('INSERT OR REPLACE INTO config VALUES (?,?)', ('host',host))
        if key:
            c.execute('INSERT OR REPLACE INTO config VALUES (?,?)', ('key',key))

def add_push(path, kind, target, city, clock):
    if kind not in ('group','private') or not str(target).isdigit() or not city.strip() or len(city)>40 or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', clock):
        raise ValueError('推送配置无效')
    with db(path) as c:
        c.execute('INSERT INTO pushes(kind,target,city,clock) VALUES (?,?,?,?)', (kind,str(target),city.strip(),clock))

def list_pushes(path=PATH):
    with db(path) as c:
        return [dict(r) for r in c.execute('SELECT id,kind,target,city,clock,last_date FROM pushes ORDER BY id')]

def delete_push(id, path=PATH):
    with db(path) as c:
        c.execute('DELETE FROM pushes WHERE id=?', (id,))

def claim_push(id, date, path=PATH):
    with db(path) as c:
        return bool(c.execute('UPDATE pushes SET last_date=? WHERE id=? AND last_date!=?', (date,id,date)).rowcount)

async def query(host, key, city, forecast=False, client=None):
    host = validate_host(host)
    if not key: raise ValueError('天气服务尚未配置')
    if not city or len(city)>40: raise ValueError('请提供城市或区县名称')
    own = client is None
    client = client or httpx.AsyncClient(timeout=8, follow_redirects=False)
    try:
        headers={'X-QW-Api-Key':key}
        r = await client.get(f'https://{host}/geo/v2/city/lookup', headers=headers, params={'location':city,'number':5,'lang':'zh'})
        check_response(r)
        locations = r.json().get('location') or []
        if not locations: raise ValueError('没有找到该地点，请提供更完整的城市或区县名')
        loc = locations[0]
        if len(locations)>1 and locations[0].get('name') == locations[1].get('name') and locations[0].get('adm1') != locations[1].get('adm1'):
            raise ValueError('存在同名地点，请加上省份或区县')
        lat, lon = float(loc['lat']), float(loc['lon'])
        if not -90<=lat<=90 or not -180<=lon<=180: raise ValueError('地点坐标无效')
        endpoint = 'daily' if forecast else 'current'
        r = await client.get(f'https://{host}/weather/v1/{endpoint}/{lat:.2f}/{lon:.2f}', headers=headers, params={'lang':'zh','localTime':'true', **({'days':3} if forecast else {})})
        check_response(r)
        data = r.json()
        name = ' '.join(dict.fromkeys(x for x in (loc.get('adm1'),loc.get('adm2'),loc.get('name')) if x))
        if forecast:
            lines = [f"{d.get('forecastStartTime','')[:10]} {d.get('daytime',{}).get('condition',{}).get('text','未知')} {d.get('temperatureMin',{}).get('value','?')}～{d.get('temperatureMax',{}).get('value','?')}℃" for d in data.get('days',[])]
            if not lines: raise ValueError('没有可用预报')
            body = '\n'.join(lines)
        else:
            x = data.get('current', data.get('weather', data))
            body = f"{x.get('condition',{}).get('text','未知')} {x.get('temperature',{}).get('value','?')}℃，体感 {x.get('feelsLike',{}).get('value','?')}℃"
        return f'{name}\n{body}\n数据来源：和风天气 https://developer.qweather.com/attribution.html'
    finally:
        if own: await client.aclose()

def check_response(response):
    if response.status_code in (401, 403): raise ValueError('天气服务认证失败或账号无权限，请检查配置')
    if response.status_code == 402: raise ValueError('天气服务账户余额或订阅异常')
    if response.status_code == 429: raise ValueError('天气服务请求过于频繁，请稍后再试')
    response.raise_for_status()
