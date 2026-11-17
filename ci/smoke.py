"""Exercise installed runtime tools and both service commands as the image user."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen


def output(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT)


def main():
    assert (os.getuid(), os.getgid()) == (568, 568), "Unexpected runtime identity"
    for tool in ("ffmpeg", "ffprobe", "mkvmerge", "mkvextract", "lsdvd", "bd_splice", "inosculate-bluray-catalog", "inosculate"):
        assert shutil.which(tool), f"Missing {tool}"
    assert "dvdvideo" in output("ffmpeg", "-hide_banner", "-demuxers")
    assert "bluray" in output("ffmpeg", "-hide_banner", "-protocols")
    output("mkvmerge", "--version")
    helper = subprocess.run(["inosculate-bluray-catalog"], capture_output=True, text=True)
    assert helper.returncode == 2 and "Usage:" in helper.stderr, helper.stderr
    assert "not found" not in output("ldd", shutil.which("inosculate-bluray-catalog"))
    with tempfile.TemporaryDirectory(dir="/config"):
        pass
    with tempfile.TemporaryDirectory(dir="/data") as directory:
        root = Path(directory)
        environment = dict(os.environ, WEB_HOST="127.0.0.1", WEB_PORT="8787", STABILITY_SECONDS="0")
        for key in ("DATA_DIR", "INGEST_DIR", "MOVIES_DIR", "TELEVISION_DIR"):
            path = root / key.lower()
            path.mkdir()
            environment[key] = str(path)
        processes = []
        try:
            # The installed entrypoint defaults to web, and must also accept worker.
            processes.append(subprocess.Popen(["inosculate"], env=environment))
            for attempt in range(60):
                assert processes[0].poll() is None, "Web process exited"
                try:
                    with urlopen("http://127.0.0.1:8787/api/health", timeout=2) as response:
                        assert json.load(response)["ok"] is True
                    break
                except URLError:
                    time.sleep(0.5)
            else:
                raise AssertionError("Web health check timed out")
            with urlopen("http://127.0.0.1:8787/", timeout=5) as response:
                html = response.read().decode()
            assets = re.findall(r'(?:src|href)="(/static/[^\"]+\.(?:js|css))"', html)
            assert len(assets) >= 2, "Built frontend assets are missing"
            for asset in assets:
                with urlopen(f"http://127.0.0.1:8787{asset}", timeout=5) as response:
                    assert response.status == 200 and len(response.read()) > 0
            log_path = root / "worker.log"
            with log_path.open("w") as log:
                processes.append(subprocess.Popen(["inosculate", "worker"], env=environment,
                                                  stdout=log, stderr=subprocess.STDOUT))
                for attempt in range(60):
                    assert processes[-1].poll() is None, log_path.read_text()
                    if "Workers started:" in log_path.read_text():
                        break
                    time.sleep(0.5)
                else:
                    raise AssertionError(log_path.read_text())
                time.sleep(1)
                assert processes[-1].poll() is None, log_path.read_text()
                assert "Traceback" not in log_path.read_text(), log_path.read_text()
            assert (Path(environment["DATA_DIR"]) / "inosculate.sqlite3").is_file()
        finally:
            for process in reversed(processes):
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    print("Runtime smoke checks passed")


if __name__ == "__main__":
    main()
