from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from functools import wraps


_active = None


class RunCancellation:
    def __init__(self):
        self.event = threading.Event()
        self.lock = threading.RLock()
        self.children = set()

    def cancel(self):
        self.event.set()
        with self.lock:
            children = list(self.children)
        for child in children:
            stop_child(child)


def stop_child(child):
    try:
        if os.name == "posix":
            os.killpg(child.pid, signal.SIGTERM)
        else:
            child.terminate()
    except (OSError, ProcessLookupError):
        pass


def check_cancelled():
    if _active is not None and _active.event.is_set():
        raise KeyboardInterrupt


def interruptible(function):
    @wraps(function)
    def run(*args, **kwargs):
        global _active
        previous, _active = _active, RunCancellation()
        controller = _active
        main_thread = threading.current_thread() is threading.main_thread()
        old_handler = signal.getsignal(signal.SIGINT) if main_thread else None

        def interrupted(_signum, _frame):
            controller.cancel()
            raise KeyboardInterrupt

        if main_thread:
            signal.signal(signal.SIGINT, interrupted)
        try:
            return function(*args, **kwargs)
        except KeyboardInterrupt:
            controller.cancel()
            progress = kwargs.get("progress")
            if progress is not None:
                progress.finish_run()
            raise
        finally:
            if main_thread:
                signal.signal(signal.SIGINT, old_handler)
            _active = previous
    return run


def run_cli_process(args, *, input=None, text=True, capture_output=True, timeout=None, check=False, env=None, cwd=None):
    """Own each CLI process group so interruption cancels the whole analysis."""
    check_cancelled()
    controller = _active
    child = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=text, env=env, cwd=cwd, start_new_session=os.name == "posix")
    if controller:
        with controller.lock:
            controller.children.add(child)
    deadline = time.monotonic() + timeout if timeout is not None else None
    first = True
    try:
        while True:
            check_cancelled()
            remaining = deadline - time.monotonic() if deadline else 0.25
            if remaining <= 0:
                raise subprocess.TimeoutExpired(args, timeout)
            try:
                stdout, stderr = child.communicate(input=input if first else None, timeout=min(0.25, remaining))
                break
            except subprocess.TimeoutExpired:
                first = False
        explicit_interrupt = child.returncode != 0 and re.search(
            r"\bSIGINT\b|\bKeyboardInterrupt\b|interrupted by (?:the )?user|user requested (?:cancellation|interruption)|^\s*(?:Error:\s*)?(?:Turn )?Interrupted\.?\s*$",
            (stdout or "")[-65536:] + "\n" + (stderr or "")[-65536:], re.I | re.M)
        if child.returncode in {-signal.SIGINT, 128 + signal.SIGINT} or explicit_interrupt:
            if controller:
                controller.cancel()
            raise KeyboardInterrupt
        check_cancelled()
        result = subprocess.CompletedProcess(args, child.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result
    finally:
        if child.poll() is None:
            stop_child(child)
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(child.pid, signal.SIGKILL)
                else:
                    child.kill()
                child.wait()
        if controller:
            with controller.lock:
                controller.children.discard(child)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                stream.close()
