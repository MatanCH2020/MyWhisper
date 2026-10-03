"""One persistent HTTP pool on a private asyncio loop; no startup requests."""
import asyncio
import concurrent.futures
import threading

import httpx


class CloudHTTP:
    def __init__(self, transport=None):
        self.loop = asyncio.new_event_loop()
        self._transport = transport
        self._client = None
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._thread.start()

    @property
    def client(self):
        # Only accessed on this loop. Neither redirects nor automatic retries
        # may send a bearer credential to a different origin.
        if self._client is None:
            self._client = httpx.AsyncClient(transport=self._transport, timeout=8,
                                             follow_redirects=False, trust_env=False)
        return self._client

    def submit(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def call(self, coroutine, timeout=10):
        future = self.submit(coroutine)
        try:
            return future.result(timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise

    def close(self):
        async def shutdown():
            pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if self._client is not None:
                await self._client.aclose()
            await self.loop.shutdown_asyncgens()
        try:
            self.call(shutdown(), 2)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self._thread.join(2)
            if not self._thread.is_alive():
                self.loop.close()
