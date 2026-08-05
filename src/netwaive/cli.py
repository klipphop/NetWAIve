import argparse

from .config import Settings
from .v06.application import V06Application
from .v06.session import SessionScope


def main() -> None:
    parser = argparse.ArgumentParser(description="NetWAIve v0.6 strict read-only resolver and deterministic planner")
    parser.add_argument("message", nargs="+", help="Intent métier à traiter")
    parser.add_argument("--confirm-write", action="store_true", help="Exécuter le plan après affichage")
    args = parser.parse_args()
    scope = SessionScope.new("cli")
    app = V06Application(Settings())
    plan = app.plan(" ".join(args.message), scope)
    print(f"Pending v0.6: {len(plan.calls)} operation(s), fingerprint={plan.fingerprint}")
    if args.confirm_write:
        report = app.confirm(scope, plan.fingerprint)
        print("Execution: " + ("success" if report.ok else "failed"))


if __name__ == "__main__":
    main()
