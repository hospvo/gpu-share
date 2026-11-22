import asyncio
import ctypes
import json
import os
import shutil
import subprocess
import time
import uuid
from contextlib import suppress
from pathlib import Path

import psutil
from aiohttp import ClientError, web
from common import clients, failure, forward, identity, read_chat


class LastInput(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("tick", ctypes.c_uint32)]


def idle_seconds():
    info = LastInput(ctypes.sizeof(LastInput), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0
    return ((ctypes.windll.kernel32.GetTickCount() - info.tick) & 0xffffffff) // 1000


def named_adapter_rows(rows, description):
    import winreg

    # DirectX retains older LUIDs. Only consider IDs present in the live sample.
    descriptions = {}
    access = winreg.KEY_READ | winreg.KEY_WOW64_64KEY
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\DirectX", 0, access) as root:
        for index in range(winreg.QueryInfoKey(root)[0]):
            try:
                with winreg.OpenKey(root, winreg.EnumKey(root, index)) as entry:
                    name = winreg.QueryValueEx(entry, "Description")[0]
                    luid = winreg.QueryValueEx(entry, "AdapterLuid")[0]
            except FileNotFoundError:
                continue
            prefix = f"luid_0x{luid >> 32:08x}_0x{luid & 0xffffffff:08x}"
            descriptions.setdefault(prefix, set()).add(name.casefold())
    return [row for row in rows if descriptions.get(row["Name"].casefold().partition("_phys_")[0])
            == {description.casefold()}]


def measure(config):
    windows_ps = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    shell = str(windows_ps) if windows_ps.is_file() else (shutil.which("pwsh") or "powershell.exe")
    command = [shell, "-NoProfile", "-NonInteractive", "-Command",
               Path(__file__).with_name("measure.ps1").read_text(encoding="utf-8")]
    result = subprocess.run(command, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
                            capture_output=True, encoding="utf-8-sig", errors="replace")
    if result.returncode:
        raise ValueError(result.stderr.strip()[:1000] or "Windows GPU counters failed")
    rows = json.loads(result.stdout)
    names = [row["Name"] for row in rows]
    if config.get("adapter"):
        rows = [row for row in rows if row["Name"].casefold() == config["adapter"].casefold()]
    elif config.get("adapter_name"):
        rows = named_adapter_rows(rows, config["adapter_name"])
    if len(rows) != 1:
        raise ValueError("Expected one GPU adapter. Available: " + (", ".join(names) or "(none)"))
    used = int(rows[0]["DedicatedUsage"]) / 1024**2
    return max(0, int(config["capacity_mib"] - used)), int(rows[0]["Load"])


async def lms(*args, timeout=180):
    proc = await asyncio.create_subprocess_exec("lms", *args, stdout=subprocess.DEVNULL,
                                                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    try:
        if await asyncio.wait_for(proc.wait(), timeout):
            raise RuntimeError("LM Studio CLI failed")
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


class Agent:
    def __init__(self, config, directory, notify=print):
        self.c = config
        self.pause = Path(directory) / "paused"
        self.games = {g.casefold() for g in config["games"]}
        self.notify = notify
        self.lm_url = "http://127.0.0.1:1234"
        self.gpu_load = 0
        self.free = 0
        self.measured = 0
        self.user = None
        self.job = None
        self.model = None
        self.game = False
        self.inventory = set()

    def local_block(self):
        if self.pause.exists():
            return "paused"
        if self.game:
            return "game"
        if time.monotonic() - self.measured > 12 or not self.measured:
            return "telemetry unavailable"
        return ""

    def state(self):
        reason = self.local_block() or ("busy" if self.user else "")
        if not reason and self.gpu_load >= self.c.get("max_gpu_percent", 80):
            reason = "GPU in use"
        available = [m for m, cost in self.c["models"].items() if m in self.inventory
                     and cost + self.c["reserve_mib"] <= self.free and not reason]
        return {"reason": reason or ("ready" if available else "model or VRAM unavailable"),
                "gpu_percent": self.gpu_load, "free_mib": self.free, "idle_seconds": idle_seconds(), "models": sorted(self.inventory),
                "available": available, "user": self.user, "active_model": self.model}

    async def monitor(self, app):
        previous_error = None
        while True:
            try:
                self.game = any((p.info["name"] or "").casefold() in self.games
                                for p in psutil.process_iter(["name"]))
                if self.job and (self.game or self.pause.exists()):
                    self.job.cancel()
                self.free, self.gpu_load = await asyncio.to_thread(measure, self.c)
                self.measured = time.monotonic()
                if previous_error:
                    print("GPU telemetry restored.", flush=True)
                previous_error = None
                if self.job and self.free < self.c["reserve_mib"]:
                    self.job.cancel()
            except (OSError, ValueError, subprocess.SubprocessError, psutil.Error, KeyError, TypeError) as exc:
                self.measured = 0
                detail = str(exc)
                if detail != previous_error:
                    print("GPU telemetry:", detail, flush=True)
                previous_error = detail
            try:
                async with app["session"].get(self.lm_url + "/v1/models",
                                               headers=self.lm_headers(), timeout=3) as reply:
                    reply.raise_for_status()
                    ids = {entry["id"] for entry in (await reply.json())["data"]}
                    self.inventory = ids.intersection(self.c["models"])
            except (ClientError, OSError, TimeoutError, ValueError, KeyError, TypeError):
                self.inventory = set()
            await asyncio.sleep(2)

    def lm_headers(self):
        key = os.getenv("LM_API_TOKEN")
        return {"Authorization": "Bearer " + key} if key else {}

    async def serve(self, request):
        if not identity(request, {"hub": self.c["key"]}):
            return failure(401, "Invalid host key")
        if request.path == "/state":
            return web.json_response(self.state())
        body = await read_chat(request)
        if body is None:
            return failure(400, "Invalid chat request")
        if body["model"] not in self.state()["available"]:
            result = failure(409, "Host unavailable")
            result.headers["X-Pool-Rejected"] = "1"
            return result
        # No await between eligibility check and reservation on this event loop.
        self.user = request.headers.get("X-Pool-User", "client")
        self.model = body["model"]
        instance = "gpu-share-" + uuid.uuid4().hex
        try:
            try:
                self.notify(f"{self.user} • {self.model}")
            except Exception as exc:
                print(f"Notification unavailable: {type(exc).__name__}", flush=True)
            await lms("load", self.model, "--identifier", instance, "--ttl", "60", "--yes",
                      "--context-length", str(self.c["context_length"]))
            if self.local_block():
                return failure(503, "Local owner needs the GPU")
            async def predict():
                async with request.app["session"].post(self.lm_url + "/v1/chat/completions",
                        headers=self.lm_headers(), json={**body, "model": instance}) as reply:
                    return await forward(request, reply)
            self.job = asyncio.create_task(predict())
            try:
                return await self.job
            except asyncio.CancelledError:
                if request.transport and not request.transport.is_closing():
                    return failure(503, "Generation interrupted by host")
                raise
        except (RuntimeError, OSError, TimeoutError, ClientError):
            return failure(502, "Local inference failed. Check LM Studio and lms.")
        finally:
            self.job = None
            try:
                await lms("unload", instance, timeout=30)
            except (RuntimeError, OSError, TimeoutError):
                print("Unload unconfirmed; LM Studio instance TTL is 60 seconds.", flush=True)
            finally:
                self.user = self.model = None
                self.measured = 0

    def app(self):
        app = web.Application(client_max_size=16 * 1024**2)
        async def monitor_context(app):
            task = asyncio.create_task(self.monitor(app))
            yield
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        app.cleanup_ctx.extend([clients, monitor_context])
        app.router.add_get("/state", self.serve)
        app.router.add_post("/run", self.serve)
        return app

