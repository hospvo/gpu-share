import asyncio
from aiohttp import ClientConnectorError, ClientError, ClientTimeout, web
from common import clients, failure, forward, identity, read_chat


def headers(node):
    return {"Authorization": "Bearer " + node["key"]}


async def probe(session, node):
    try:
        async with session.get(node["url"] + "/state", headers=headers(node),
                               allow_redirects=False, timeout=ClientTimeout(total=3)) as reply:
            if reply.status == 200:
                return dict(await reply.json(), host=node["name"])
    except (ClientError, OSError, TimeoutError, ValueError):
        pass
    return {"host": node["name"], "reason": "offline", "models": []}


def create(config):
    nodes = config["nodes"]

    async def route(request):
        user = identity(request, config["users"])
        if not user:
            return failure(401, "Invalid API key")
        session = request.app["session"]
        states = await asyncio.gather(*(probe(session, n) for n in nodes))
        if request.path == "/v1/hosts":
            return web.json_response({"data": states})
        if request.path == "/v1/models":
            models = sorted({m for s in states for m in s["models"]})
            return web.json_response({"object": "list", "data": [
                {"id": m, "object": "model", "owned_by": "gpu-share"} for m in models]})
        body = await read_chat(request)
        if body is None:
            return failure(400, "Expected model and messages")
        candidates = [(node, state) for node, state in zip(nodes, states)
                      if body["model"] in state.get("available", [])]
        candidates.sort(key=lambda pair: (pair[1]["idle_seconds"] >= 300,
                                           pair[1]["free_mib"]), reverse=True)
        for node, _ in candidates:
            try:
                async with session.post(node["url"] + "/run", json=body, allow_redirects=False,
                                        headers={**headers(node), "X-Pool-User": user}) as reply:
                    if reply.status == 409 and reply.headers.get("X-Pool-Rejected") == "1":
                        continue
                    return await forward(request, reply)
            except ClientConnectorError:
                continue
            except (OSError, ClientError, TimeoutError):
                if request.transport and not request.transport.is_closing():
                    return failure(502, "Host connection interrupted")
                raise
        return failure(503, "No available host for this model. See /v1/hosts and retry.")

    app = web.Application(client_max_size=16 * 1024**2)
    app.cleanup_ctx.append(clients)
    app.router.add_get("/v1/models", route)
    app.router.add_get("/v1/hosts", route)
    app.router.add_post("/v1/chat/completions", route)
    return app

