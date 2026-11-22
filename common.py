import asyncio
import hmac
import json
from aiohttp import ClientSession, ClientTimeout, web


def failure(status, message):
    return web.json_response({"error": {"message": message, "type": "gpu_share_error"}}, status=status)


def identity(request, keys):
    supplied = request.headers.get("Authorization", "").encode()
    for name, key in keys.items():
        if key and hmac.compare_digest(supplied, ("Bearer " + key).encode()):
            return name
    return None


async def clients(app):
    async with ClientSession(timeout=ClientTimeout(total=1800, sock_connect=4, sock_read=600)) as session:
        app["session"] = session
        yield


async def forward(request, upstream):
    result = web.StreamResponse(status=upstream.status, headers={
        "Content-Type": upstream.headers.get("Content-Type", "application/json"),
        "Cache-Control": "no-store"
    })
    await result.prepare(request)
    try:
        async for chunk in upstream.content.iter_any():
            await result.write(chunk)
        await result.write_eof()
    except BaseException:
        if request.transport:
            request.transport.abort()
        raise
    return result


async def read_chat(request):
    try:
        body = await request.json()
        if isinstance(body, dict) and isinstance(body.get("model"), str) and isinstance(body.get("messages"), list):
            return body
    except (ValueError, UnicodeError):
        pass
    return None

