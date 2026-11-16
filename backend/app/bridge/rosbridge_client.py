"""Minimal asyncio rosbridge v2 protocol client (JSON over WebSocket).

Supports advertise / publish / subscribe / call_service with automatic reconnection and
re-subscription. Used by the BridgeManager to talk to the natively running ROS 2 stack.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from websockets.asyncio.client import connect

log = logging.getLogger("arcadia.rosbridge")
Handler = Callable[[dict], Awaitable[None] | None]


class RosBridgeClient:
    def __init__(self, url: str, reconnect_s: float = 3.0) -> None:
        self.url = url
        self.reconnect_s = reconnect_s
        self._ws = None
        self._subs: dict[str, tuple[str, Handler, dict]] = {}
        self._advertised: dict[str, str] = {}
        self._pending: dict[str, asyncio.Future] = {}
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._connected = asyncio.Event()
        self._seq = 0
        self._failures = 0
        self.on_connect: Callable[[], Awaitable[None] | None] | None = None
        self.on_disconnect: Callable[[], Awaitable[None] | None] | None = None

    # ----------------------------------------------------------- lifecycle
    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    async def try_connect(self, timeout: float = 4.0) -> bool:
        """One-shot probe used for auto mode selection."""
        try:
            async with asyncio.timeout(timeout):
                ws = await connect(self.url, max_size=16 * 1024 * 1024, open_timeout=timeout)
                await ws.close()
            return True
        except Exception as e:  # noqa: BLE001
            log.debug("rosbridge probe failed: %s", e)
            return False

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="rosbridge-client")

    async def stop(self) -> None:
        self._stop.set()
        if self._ws is not None:
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                async with connect(self.url, max_size=16 * 1024 * 1024, ping_interval=20, ping_timeout=20) as ws:
                    self._ws = ws
                    self._connected.set()
                    self._failures = 0
                    log.info("Connected to rosbridge at %s", self.url)
                    for topic, mtype in self._advertised.items():
                        await self._send({"op": "advertise", "topic": topic, "type": mtype})
                    for topic, (mtype, _h, opts) in self._subs.items():
                        await self._send({"op": "subscribe", "topic": topic, "type": mtype, **opts})
                    if self.on_connect:
                        r = self.on_connect()
                        if asyncio.iscoroutine(r):
                            await r
                    async for raw in ws:
                        await self._dispatch(raw)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                if not self._stop.is_set():
                    self._failures += 1
                    if self._failures in (1, 5) or self._failures % 100 == 0:
                        log.warning("rosbridge unreachable (%s); retrying every %.0fs (attempt %d)", e, self._retry_delay(),
                                    self._failures)
            finally:
                was = self._connected.is_set()
                self._connected.clear()
                self._ws = None
                if was and self.on_disconnect:
                    # A failing callback (e.g. Redis briefly down) must never end this loop, or the
                    # client would stop retrying for good.
                    try:
                        r = self.on_disconnect()
                        if asyncio.iscoroutine(r):
                            await r
                    except Exception:  # noqa: BLE001
                        log.exception("rosbridge on_disconnect callback failed")
            if not self._stop.is_set():
                await asyncio.sleep(self._retry_delay())

    def _retry_delay(self) -> float:
        # back off to 15 s while the native stack is down so auto-mode probing stays cheap
        return min(15.0, self.reconnect_s * (1 + self._failures // 3))

    # ----------------------------------------------------------- protocol
    async def _send(self, msg: dict) -> None:
        if self._ws is None:
            raise ConnectionError("rosbridge not connected")
        await self._ws.send(json.dumps(msg, separators=(",", ":")))

    async def _dispatch(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        op = msg.get("op")
        if op == "publish":
            sub = self._subs.get(msg.get("topic", ""))
            if sub:
                # a handler failure (database or Redis hiccup) is not a rosbridge failure: keep the link
                try:
                    r = sub[1](msg.get("msg", {}))
                    if asyncio.iscoroutine(r):
                        await r
                except Exception:  # noqa: BLE001
                    log.exception("handler for %s failed", msg.get("topic"))
        elif op == "service_response":
            fut = self._pending.pop(msg.get("id", ""), None)
            if fut and not fut.done():
                if msg.get("result", True):
                    fut.set_result(msg.get("values", {}))
                else:
                    fut.set_exception(RuntimeError(str(msg.get("values"))))
        elif op == "status" and msg.get("level") in ("error", "warning"):
            log.warning("rosbridge status: %s", msg.get("msg"))

    async def advertise(self, topic: str, msg_type: str) -> None:
        self._advertised[topic] = msg_type
        if self.connected:
            await self._send({"op": "advertise", "topic": topic, "type": msg_type})

    async def publish(self, topic: str, msg: dict[str, Any]) -> bool:
        if not self.connected:
            return False
        if topic not in self._advertised:
            log.warning("publishing to un-advertised topic %s", topic)
        try:
            await self._send({"op": "publish", "topic": topic, "msg": msg})
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("publish failed: %s", e)
            return False

    async def subscribe(self, topic: str, msg_type: str, handler: Handler, throttle_ms: int = 0,
                        queue_length: int = 1) -> None:
        opts = {"throttle_rate": throttle_ms, "queue_length": queue_length}
        self._subs[topic] = (msg_type, handler, opts)
        if self.connected:
            await self._send({"op": "subscribe", "topic": topic, "type": msg_type, **opts})

    async def call_service(self, service: str, args: dict | None = None, timeout: float = 5.0) -> dict:
        self._seq += 1
        cid = f"svc_{self._seq}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[cid] = fut
        await self._send({"op": "call_service", "service": service, "args": args or {}, "id": cid})
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(cid, None)
