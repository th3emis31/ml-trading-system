from __future__ import annotations

import argparse
import os

from run import main as run_main


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SmartEntryProAI launcher")
    parser.add_argument("--host", default=os.environ.get("FLASK_HOST", "0.0.0.0"), help="Server host")
    parser.add_argument("--port", type=int, default=int(os.environ.get("FLASK_PORT", "5001")), help="Server port")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.environ["FLASK_HOST"] = str(args.host)
    os.environ["FLASK_PORT"] = str(args.port)
    if args.debug:
        os.environ["FLASK_DEBUG"] = "1"
    run_main()


if __name__ == "__main__":
    main()
