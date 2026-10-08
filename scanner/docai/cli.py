import argparse
import json
import logging
import sys
import requests
from docai.core.scanner import Scanner


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument("--commit", help="Commit to scan (endpoints impacted by it)")
    parser.add_argument("--all", action="store_true", help="Every endpoint of the folder, no git needed")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--server", help="DocAI server URL")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show debug logs")

    args = parser.parse_args()

    if not args.commit and not args.all:
        parser.error("either --commit or --all is required")

    # Logs go to stderr so stdout stays valid JSON.
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr
    )

    scanner = Scanner()

    if args.all:
        result = scanner.scan_full(args.repo, label=args.commit or "local")
    else:
        result = scanner.scan(
            repo_path=args.repo,
            commit=args.commit
        )

    if args.server:

        try:

            response = requests.post(
                f"{args.server}/analyze",
                json=result,
                timeout=60
            )
            response.raise_for_status()

        except Exception as e:

            print("Failed to send results to server:", e, file=sys.stderr)

    else:

        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
