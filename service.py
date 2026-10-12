"""Run receiver and chat worker on one Railway volume; exit if either fails."""
import os
import signal
import subprocess
import sys
import time


def main():
    chat = os.environ.get('CHAT_ENABLED') == 'true'
    review = os.environ.get('REVIEW_ENABLED') == 'true'
    # review.py serves chat mentions too, so it takes over whenever reviews are on.
    target = 'review:create_app()' if review else 'chat:create_app()' if chat else 'intake:create_app()'
    commands = [['gunicorn', '--bind', '0.0.0.0:8080', '--workers', '1', '--threads', '4', target]]
    if review:
        from review import config
        config()  # Validate before starting either child.
        commands.append([sys.executable, 'review.py'])
    elif chat:
        from chat import config
        config()  # Validate before starting either child.
        commands.append([sys.executable, 'chat.py'])
    children = []
    def stop(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for command in commands:
            children.append(subprocess.Popen(command))
        while all(p.poll() is None for p in children):
            time.sleep(0.5)
        raise SystemExit(1)
    finally:
        for p in children:
            if p.poll() is None:
                p.terminate()
        for p in children:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()


if __name__ == '__main__':
    main()
