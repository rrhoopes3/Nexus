"""
Nexus CLI entry point — run with `python -m nexus`.
"""
import argparse
import logging
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="nexus",
        description="Nexus — intent-driven programming language",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging"
    )

    sub = parser.add_subparsers(dest="command")

    # nexus new <name>
    p_new = sub.add_parser("new", help="Create a new Nexus project")
    p_new.add_argument("name", help="Project name")
    p_new.add_argument("--path", help="Parent directory (default: current)")

    # nexus run
    p_run = sub.add_parser("run", help="Compile intents and run the project")
    p_run.add_argument("--no-fix", action="store_true", help="Disable auto-fix on compiler errors")
    p_run.add_argument("-w", "--watch", action="store_true", help="Watch for changes and auto-recompile")

    # nexus intent "<text>"
    p_intent = sub.add_parser("intent", help="One-shot: compile an intent to Rust")
    p_intent.add_argument("text", help="Natural language intent")

    # nexus show
    sub.add_parser("show", help="Show last generated Rust code")

    # nexus verify
    sub.add_parser("verify", help="Explain what the generated code does")

    # nexus tighten
    sub.add_parser("tighten", help="Analyze code and suggest type improvements")

    # nexus history
    p_hist = sub.add_parser("history", help="Show audit trail of AI transformations")
    p_hist.add_argument("-n", "--limit", type=int, default=20, help="Number of entries")

    # nexus profile
    sub.add_parser("profile", help="Show developer profile with style and anti-patterns")

    # nexus style
    sub.add_parser("style", help="Show learned coding style preferences")

    # nexus types
    sub.add_parser("types", help="Show type tracking pipeline status")

    # nexus forget <pattern>
    p_forget = sub.add_parser("forget", help="Forget a learned pattern (tombstone it)")
    p_forget.add_argument("pattern", help="Pattern name to forget")

    # nexus reset-profile
    sub.add_parser("reset-profile", help="Reset the entire developer profile")

    # nexus serve
    p_serve = sub.add_parser("serve", help="Generate and serve a live API from intents")
    p_serve.add_argument("-p", "--port", type=int, default=8080, help="Port (default: 8080)")

    # nexus watch
    sub.add_parser("watch", help="Watch .nx files and auto-recompile on changes")

    # nexus repl
    sub.add_parser("repl", help="Interactive intent REPL")

    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(name)s %(levelname)s: %(message)s")

    if not args.command:
        parser.print_help()
        sys.exit(0)

    from nexus.cli import (
        cmd_new, cmd_run, cmd_intent, cmd_show, cmd_verify,
        cmd_tighten, cmd_history, cmd_profile, cmd_style,
        cmd_types, cmd_forget, cmd_reset_profile, cmd_repl,
    )

    commands = {
        "new": lambda: cmd_new(args.name, args.path),
        "run": lambda: cmd_run(auto_fix=not args.no_fix, watch_mode=args.watch),
        "intent": lambda: cmd_intent(args.text),
        "show": cmd_show,
        "verify": cmd_verify,
        "tighten": cmd_tighten,
        "history": lambda: cmd_history(limit=args.limit),
        "profile": cmd_profile,
        "style": cmd_style,
        "types": cmd_types,
        "forget": lambda: cmd_forget(args.pattern),
        "reset-profile": cmd_reset_profile,
        "watch": lambda: cmd_run(auto_fix=True, watch_mode=True),
        "repl": cmd_repl,
    }

    if args.command == "serve":
        from nexus.server import NexusServer
        from nexus.project import load_project
        project = load_project()
        NexusServer(project.root, port=args.port).start()
    elif args.command in commands:
        commands[args.command]()


if __name__ == "__main__":
    main()
