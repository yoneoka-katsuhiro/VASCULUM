from __future__ import annotations

import sys
import threading
import time
from contextlib import contextmanager


class TerminalProgress:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def update(self, message: str) -> None:
        if self.enabled:
            print(message, flush=True)

    @contextmanager
    def activity(self, message: str):
        if not self.enabled:
            yield
            return

        stop = threading.Event()
        started = time.monotonic()
        stream = sys.stderr
        dynamic = stream.isatty()

        def animate() -> None:
            frames = "|/-\\"
            frame_index = 0
            while not stop.is_set():
                elapsed = int(time.monotonic() - started)
                text = f"{frames[frame_index % len(frames)]} {message} | {elapsed}s"
                if dynamic:
                    stream.write(f"\r{text}")
                    stream.flush()
                elif elapsed == 0 or elapsed % 5 == 0:
                    print(text, file=stream, flush=True)
                frame_index += 1
                stop.wait(1.0)

        thread = threading.Thread(target=animate, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=2.0)
            elapsed = int(time.monotonic() - started)
            if dynamic:
                stream.write("\r\033[K")
            print(f"Done: {message} | {elapsed}s", file=stream, flush=True)
