"""Run the standalone p-OPS web receiver and worker as one hosted service."""
import signal
import subprocess
import sys
import time

from p_ops_runtime import config


def main():
    config()
    commands = [
        ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "p_ops_runtime:create_app()"],
        [sys.executable, "p_ops_runtime.py"],
    ]
    children = []
    def stop(*_): raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    try:
        for command in commands: children.append(subprocess.Popen(command))
        while all(process.poll() is None for process in children): time.sleep(0.5)
        raise SystemExit(1)
    finally:
        for process in children:
            if process.poll() is None: process.terminate()
        for process in children:
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()


if __name__ == "__main__":
    main()
