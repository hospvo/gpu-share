import asyncio
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from agent import Agent
from hub import create


class PoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.cli = self.enterContext(patch("agent.lms", new_callable=AsyncMock))
        self.enterContext(patch("agent.idle_seconds", return_value=600))
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.notifications = []
        async def chat(req):
            body = await req.json()
            self.started.set()
            if body.get("wait"):
                await self.release.wait()
            if body.get("stream"):
                return web.Response(body=b'data: [DONE]\n\n', content_type="text/event-stream")
            return web.json_response({"choices": [{"message": {"content": "hello"}}]})
        lm = web.Application()
        lm.router.add_post("/v1/chat/completions", chat)
        lm_server = await self.server(lm)
        cfg = {"key": "internal", "models": {"model": 4000}, "games": [], "reserve_mib": 1024,
               "context_length": 4096}
        self.agent = Agent(cfg, self.folder.name, self.notifications.append)
        self.agent.lm_url = str(lm_server.make_url("")).rstrip("/")
        self.agent.inventory = {"model"}
        self.agent.free = 8192
        self.agent.measured = time.monotonic()
        # Inject deterministic hardware samples while exercising the real HTTP handlers.
        self.agent.monitor = lambda _: asyncio.sleep(600)
        host = await self.server(self.agent.app())
        hub = create({"users": {"alice": "personal"}, "nodes": [
            {"name": "PC", "url": str(host.make_url("")).rstrip("/"), "key": "internal"}]})
        self.client = TestClient(TestServer(hub))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.auth = {"Authorization": "Bearer personal"}

    async def server(self, app):
        server = TestServer(app)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        return server

    async def send(self, **options):
        return await self.client.post("/v1/chat/completions", headers=self.auth,
                                      json={"model": "model", "messages": [], **options})

    async def test_stream_identity_and_release(self):
        reply = await self.send(stream=True)
        self.assertEqual(await reply.text(), "data: [DONE]\n\n")
        self.assertEqual(self.notifications, ["alice • model"])
        self.assertEqual(self.cli.await_args_list[-1].args[:1], ("unload",))
        self.assertEqual(self.cli.await_args_list[0].args[3], self.cli.await_args_list[-1].args[1])

    async def test_changes_in_capacity(self):
        for attribute, value in [("game", True), ("free", 4999), ("gpu_load", 99), ("measured", 0)]:
            with self.subTest(attribute=attribute), patch.object(self.agent, attribute, value):
                self.assertEqual((await self.send()).status, 503)
        self.agent.pause.touch()
        self.assertEqual((await self.send()).status, 503)
        self.agent.pause.unlink()
        self.assertEqual((await self.send()).status, 200)

    async def test_busy_and_recovery(self):
        first = asyncio.create_task(self.send(wait=True))
        await asyncio.wait_for(self.started.wait(), 3)
        try:
            self.assertEqual((await self.send()).status, 503)
            self.agent.job.cancel()
            self.assertEqual((await first).status, 503)
        finally:
            self.release.set()
        self.assertIsNone(self.agent.user)

    async def test_integration(self):
        self.assertEqual((await self.client.get("/v1/models")).status, 401)
        reply = await self.client.get("/v1/models", headers=self.auth)
        self.assertEqual((await reply.json())["data"][0]["id"], "model")
        reply = await self.client.get("/v1/hosts", headers=self.auth)
        self.assertEqual((await reply.json())["data"][0]["available"], ["model"])

