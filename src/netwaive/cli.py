import argparse

from .agent import NetBoxAgent
from .config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Assistant LLM NetBox via pynetbox")
    parser.add_argument("message", nargs="+", help="Question ou action à traiter")
    parser.add_argument("--confirm-write", action="store_true", help="Autoriser les outils RW")
    args = parser.parse_args()
    response = NetBoxAgent(Settings()).run(" ".join(args.message), confirm_write=args.confirm_write)
    print(response.message)


if __name__ == "__main__":
    main()
