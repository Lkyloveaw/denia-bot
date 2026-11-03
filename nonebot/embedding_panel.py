"""Authenticated dashboard configuration for the SiliconFlow embedding key."""
import json
import os
import tempfile

from fastapi import HTTPException, Request


def register(app, *, authorized, mutate, root):
    path = root / 'data' / 'embedding_secrets.json'

    @app.get('/api/embedding')
    def embedding_status(req: Request):
        authorized(req)
        try:
            key = json.loads(path.read_text(encoding='utf-8')).get('api_key', '')
        except (OSError, ValueError, AttributeError):
            key = ''
        return {'provider': 'SiliconFlow', 'model': 'BAAI/bge-m3', 'configured': bool(key)}

    @app.post('/api/embedding')
    async def embedding_save(req: Request):
        mutate(req)
        body = await req.json()
        key = body.get('api_key') if isinstance(body, dict) else None
        if not isinstance(key, str) or not key.startswith('sk-') or not 8 <= len(key) <= 512 or any(c.isspace() for c in key):
            raise HTTPException(400, 'API Key 格式无效')
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix='.embedding-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump({'api_key': key}, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return {'ok': True, 'configured': True}
