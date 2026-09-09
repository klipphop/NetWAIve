import argparse

from .copilot import build_agent
from .config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="NetBox MCP assistant")
    parser.add_argument("message", nargs="+", help="Question ou action")
    parser.add_argument("--confirm-write", action="store_true", help="Exécuter le Change Plan après validation")
    args = parser.parse_args()
    agent = build_agent(Settings())
    result = agent.run(" ".join(args.message))
    if result.pending_confirmation and args.confirm_write:
        result = agent.confirm(result.pending_confirmation)
    print(result.message)


if __name__ == "__main__":
    main()
