from __future__ import annotations

import os
import queue
import re
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO


ProgressCallback = Callable[[float | None, str], None]
CancelCallback = Callable[[], bool]


MKVMERGE_PROGRESS = re.compile(r"Progress:\s*(\d+)%")


def run_process(
    command: list[str],
    on_output: ProgressCallback,
    should_cancel: CancelCallback,
    cwd: Path | None = None,
    stdin: BinaryIO | None = None,
) -> int:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        errors="replace",
        start_new_session=True,
    )
    output_queue: queue.Queue[str | None] = queue.Queue()

    def reader() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            output_queue.put(line.rstrip())
        output_queue.put(None)

    threading.Thread(target=reader, daemon=True).start()
    stream_done = False
    try:
        while process.poll() is None or not stream_done:
            if should_cancel() and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
            try:
                line = output_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if line is None:
                stream_done = True
                continue
            progress = None
            match = MKVMERGE_PROGRESS.search(line)
            if match:
                progress = float(match.group(1))
            on_output(progress, line)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        if process.stdout:
            process.stdout.close()
    return process.returncode

