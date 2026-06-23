import sys

from .db import initialize
from .web import serve
from .worker import run_worker


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "web"
    if command == "init":
        initialize()
    elif command == "web":
        initialize()
        serve()
    elif command == "worker":
        initialize()
        run_worker()
    else:
        raise SystemExit(f"Unknown command: {command}")


if __name__ == "__main__":
    main()

