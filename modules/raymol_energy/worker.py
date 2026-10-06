"""One active helper operation; cancellation is read independently of native work."""

import json
import queue
import sys
import threading
from typing import Any
from pydantic import Field

from .protocol import BaseModelNoExtra, Message, MAX_MESSAGE_BYTES
from .runtime import Runtime


class Worker(BaseModelNoExtra):
    jobs: Any = Field(default_factory=lambda: queue.Queue(maxsize=1))
    lock: Any = Field(default_factory=threading.Lock)
    active: Any = None
    generation: int | None = None
    last_request: int = 0
    analysis: Any = None

    def read(self):
        try:
            while True:
                line = sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 1)
                if not line:
                    return
                if len(line) > MAX_MESSAGE_BYTES or not line.endswith(b"\n"):
                    raise ValueError("Oversized or truncated message")
                message = Message.model_validate_json(line)
                with self.lock:
                    if message.kind == "cancel":
                        if self.active and (message.generation, message.request) == (self.active[0].generation, self.active[0].request):
                            self.active[1].set()
                    elif message.kind == "run":
                        if self.active or message.request <= self.last_request or self.generation not in {None, message.generation}:
                            raise ValueError("Busy, stale request, or wrong generation")
                        self.generation, self.last_request = message.generation, message.request
                        self.active = (message, threading.Event())
                        self.jobs.put_nowait(self.active)
                    else:
                        raise ValueError("Unexpected message direction")
        except Exception as error:
            print(f"protocol: {error}", file=sys.stderr, flush=True)
        finally:
            with self.lock:
                if self.active:
                    self.active[1].set()
            self.jobs.put(None)

    @staticmethod
    def emit(message, kind, payload=None):
        reply = Message(generation=message.generation, request=message.request, kind=kind, payload={} if payload is None else payload)
        line = json.dumps(reply.model_dump(exclude_none=True), allow_nan=False).encode() + b"\n"
        if len(line) > MAX_MESSAGE_BYTES:
            raise ValueError("Response exceeds message limit")
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()

    def run(self):
        threading.Thread(target=self.read, daemon=True).start()
        while (job := self.jobs.get()) is not None:
            message, cancelled = job
            self.emit(message, "started")
            try:
                if message.operation == "analyze":
                    if self.analysis is None:
                        from .analysis import Analysis
                        self.analysis = Analysis()
                    result = self.analysis.run(message.payload, cancelled, lambda payload: self.emit(message, "stage", payload))
                elif message.operation == "release":
                    self.analysis = None
                    result = {"released": True}
                else:
                    result = Runtime.execute(message.operation, message.payload, cancelled)
                with self.lock:
                    kind = "cancelled" if cancelled.is_set() else "result"
                    self.active = None
                self.emit(message, kind, {} if kind == "cancelled" else result)
            except Exception as error:
                with self.lock:
                    self.active = None
                self.emit(message, "error", {"message": str(error)[:2000]})


if __name__ == "__main__":
    Worker().run()
