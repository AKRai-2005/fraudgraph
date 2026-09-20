"""Server-sent events: watch an investigation while it runs.

An answer file is the *result* of an investigation. It says nothing about how
the agent got there -- which query it ran first, what that returned, what it
decided to look at next, or where it stopped. All of that already exists as
the timeline and the tool ledger; it was simply only readable afterwards.

This streams it as it happens. The agent runs on a worker thread with two
observers attached -- one on each timeline entry, one on each graph call --
that push onto a queue the request drains as SSE. The observers are listeners
only: they cannot change a verdict, and their exceptions are swallowed inside
the agent, so a browser disconnecting mid-investigation cannot corrupt a case.

The investigation still finishes and is still written to the working record
even if the client goes away, because a half-run case in the console would be
worse than no case at all.
"""
from __future__ import annotations

import json
import queue
import threading
import traceback
from datetime import datetime, timezone
from typing import Any, Iterator

HEARTBEAT_S = 15.0
# A guard against a wedged backend holding a browser connection open for ever.
# A full investigation is 9-13 graph calls; against a cold Savanna workspace
# with an LLM planner in the loop, the slowest observed run was about 40s.
MAX_RUN_S = 180.0


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


class InvestigationStream:
    """Runs one investigation on a thread and yields its steps as SSE."""

    def __init__(self, service, case_id: str, runner):
        self.service = service
        self.case_id = case_id
        self.runner = runner            # callable(on_step, on_call) -> record dict
        self.q: queue.Queue = queue.Queue(maxsize=512)
        self.result: dict | None = None
        self.error: str | None = None

    # -------------------------------------------------------------- observers
    def _put(self, event: str, payload: dict) -> None:
        try:
            self.q.put_nowait((event, payload))
        except queue.Full:
            # A slow reader must not stall the investigation. Dropping a
            # progress frame is harmless; the full timeline is in the record
            # that the `done` event carries.
            pass

    def on_step(self, entry) -> None:
        self._put("step", {
            "step": entry.step, "kind": entry.kind, "detail": entry.detail,
            "at": datetime.now(timezone.utc).isoformat(),
        })

    def on_call(self, call) -> None:
        self._put("query", {
            "step": call.step, "ref": call.ref, "backend": call.backend,
            "ok": call.ok, "duration_ms": call.duration_ms,
            "summary": call.result_summary, "error": call.error,
        })

    # ------------------------------------------------------------------- run
    def _work(self) -> None:
        try:
            self.result = self.runner(self.on_step, self.on_call)
        except Exception as exc:  # noqa: BLE001 - reported to the client as an event
            self.error = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        finally:
            self._put("__done__", {})

    def events(self) -> Iterator[str]:
        thread = threading.Thread(target=self._work, name=f"investigate-{self.case_id}",
                                  daemon=True)
        started = datetime.now(timezone.utc)
        thread.start()
        yield _sse("open", {"case_id": self.case_id,
                            "backend": self.service.store.backend_name,
                            "at": started.isoformat()})
        while True:
            try:
                event, payload = self.q.get(timeout=HEARTBEAT_S)
            except queue.Empty:
                elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                if elapsed > MAX_RUN_S:
                    yield _sse("error", {"message": f"investigation exceeded {MAX_RUN_S:.0f}s"})
                    return
                # SSE comment: keeps proxies and the browser from timing out
                yield f": heartbeat {elapsed:.0f}s\n\n"
                continue
            if event == "__done__":
                break
            yield _sse(event, payload)
        thread.join(timeout=5.0)
        if self.error:
            yield _sse("error", {"message": self.error})
            return
        yield _sse("done", self.result or {})
