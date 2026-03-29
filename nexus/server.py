"""
Nexus server — intent-to-live-API with runtime modification.

Generates a Flask API from .nx intents, serves it, and exposes
/_nexus/* endpoints for runtime intent injection and introspection.
"""
from __future__ import annotations

import importlib
import json
import logging
import re
import sys
import textwrap
import threading
import time
import traceback
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

from nexus.audit import log_transform
from nexus.intent import _strip_markdown_fences
from nexus.llm import llm_call
from nexus.profile import log_intent
from nexus.prompts import API_GENERATE, API_MODIFY, API_DESCRIBE

log = logging.getLogger("nexus.server")


class NexusServer:
    """Live API server powered by intent-driven code generation.

    Generates a Flask app from intents, serves it, and supports
    runtime modification via the /_nexus/intent endpoint.
    """

    def __init__(self, project_root: Path, port: int = 8080):
        self.project_root = project_root
        self.port = port
        self.nexus_dir = project_root / ".nexus"
        self.nexus_dir.mkdir(parents=True, exist_ok=True)

        self.generated_path = self.nexus_dir / "build" / "api_server.py"
        self.generated_path.parent.mkdir(parents=True, exist_ok=True)

        self.audit_path = self.nexus_dir / "audit.jsonl"
        self.profile_path = self.nexus_dir / "profile.jsonl"
        self.intents_path = self.nexus_dir / "api_intents.jsonl"

        self._app = None
        self._api_source = ""
        self._intents: list[str] = []
        self._lock = threading.Lock()
        self._boot_time = datetime.now(timezone.utc)
        self._reload_count = 0

    # ── Intent Management ─────────────────────────────────────────────────

    def _load_intents_from_nx(self) -> list[str]:
        """Load API intents from .nx files in the project."""
        src_dir = self.project_root / "src"
        if not src_dir.exists():
            return []

        intents = []
        for nx_path in sorted(src_dir.rglob("*.nx")):
            text = nx_path.read_text(encoding="utf-8")
            for line in text.splitlines():
                line = line.strip()
                if line.lower().startswith("intent:"):
                    intent = line[7:].strip()
                    if intent:
                        intents.append(intent)
        return intents

    def _save_intent_log(self, intent: str, source: str = "runtime") -> None:
        """Log an intent to the intents journal."""
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "intent": intent,
            "source": source,
        }
        with open(self.intents_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ── Code Generation ───────────────────────────────────────────────────

    def _generate_api(self, intents: list[str]) -> str:
        """Generate Flask API code from intents."""
        prompt = "Generate a Flask API with these behaviors:\n"
        for i, intent in enumerate(intents, 1):
            prompt += f"{i}. {intent}\n"

        response = llm_call(
            messages=[{"role": "user", "content": prompt}],
            system=API_GENERATE,
            max_tokens=8192,
        )
        return _strip_markdown_fences(response)

    def _modify_api(self, current_source: str, new_intent: str) -> str:
        """Modify existing API code with a new intent."""
        prompt = (
            f"Current API code:\n```python\n{current_source}\n```\n\n"
            f"New intent: {new_intent}"
        )
        response = llm_call(
            messages=[{"role": "user", "content": prompt}],
            system=API_MODIFY,
            max_tokens=8192,
        )
        return _strip_markdown_fences(response)

    def _describe_api(self, source: str) -> str:
        """Generate API documentation from source code."""
        response = llm_call(
            messages=[{"role": "user", "content": source}],
            system=API_DESCRIBE,
            temperature=0.1,
        )
        return response.strip()

    # ── App Loading ───────────────────────────────────────────────────────

    def _load_app_from_source(self, source: str) -> Any:
        """Execute generated source and extract the Flask app.

        Returns the Flask app or raises on failure.
        """
        # Write to disk for debugging
        self.generated_path.write_text(source, encoding="utf-8")

        # Execute in isolated namespace with a proper __name__
        namespace: dict[str, Any] = {"__name__": "nexus_api", "__file__": str(self.generated_path)}
        exec(compile(source, str(self.generated_path), "exec"), namespace)

        # Look for create_app() factory
        if "create_app" in namespace:
            app = namespace["create_app"]()
        elif "app" in namespace:
            app = namespace["app"]
        else:
            raise RuntimeError(
                "Generated code must define create_app() or a top-level 'app' variable"
            )

        return app

    def _wrap_with_nexus_routes(self, app: Any) -> Any:
        """Add /_nexus/* management endpoints to the app."""
        from flask import Flask, jsonify, request as flask_request

        server = self  # capture for closures

        @app.route("/_nexus/status", methods=["GET"])
        def nexus_status():
            uptime = (datetime.now(timezone.utc) - server._boot_time).total_seconds()
            return jsonify({
                "status": "running",
                "intents": len(server._intents),
                "reloads": server._reload_count,
                "uptime_seconds": int(uptime),
                "generated_file": str(server.generated_path),
            })

        @app.route("/_nexus/intent", methods=["POST"])
        def nexus_inject_intent():
            data = flask_request.get_json(silent=True) or {}
            intent = data.get("intent", "").strip()
            if not intent:
                return jsonify({"error": "No intent provided"}), 400

            try:
                result = server.inject_intent(intent)
                return jsonify(result)
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @app.route("/_nexus/intents", methods=["GET"])
        def nexus_list_intents():
            return jsonify({"intents": server._intents})

        @app.route("/_nexus/source", methods=["GET"])
        def nexus_show_source():
            return jsonify({"source": server._api_source})

        @app.route("/_nexus/describe", methods=["GET"])
        def nexus_describe():
            if not server._api_source:
                return jsonify({"description": "No API generated yet."})
            desc = server._describe_api(server._api_source)
            return jsonify({"description": desc})

        @app.route("/_nexus/audit", methods=["GET"])
        def nexus_audit():
            from nexus.audit import get_history
            entries = get_history(server.audit_path, limit=50)
            return jsonify({"entries": entries})

        return app

    # ── Runtime Intent Injection ──────────────────────────────────────────

    def inject_intent(self, intent: str) -> dict:
        """Inject a new intent at runtime and hot-reload the API."""
        with self._lock:
            log.info("Injecting intent: %s", intent)
            before = self._api_source

            # Modify existing API
            new_source = self._modify_api(self._api_source, intent)

            # Try to load the new app
            try:
                new_app = self._load_app_from_source(new_source)
                new_app = self._wrap_with_nexus_routes(new_app)
            except Exception as e:
                log.error("Intent injection failed: %s", e)
                return {
                    "success": False,
                    "error": str(e),
                    "intent": intent,
                }

            # Success — swap the app
            self._intents.append(intent)
            self._api_source = new_source
            self._app = new_app
            self._reload_count += 1

            # Log everything
            self._save_intent_log(intent, source="runtime")
            log_intent(self.profile_path, intent)
            log_transform(
                self.audit_path, "api_modify", "runtime",
                before=before, after=new_source,
                metadata={"intent": intent},
            )

            log.info("Intent applied. Reload #%d.", self._reload_count)
            return {
                "success": True,
                "intent": intent,
                "reload_count": self._reload_count,
                "total_intents": len(self._intents),
            }

    # ── Server Lifecycle ──────────────────────────────────────────────────

    def build(self) -> None:
        """Generate the initial API from .nx file intents."""
        self._intents = self._load_intents_from_nx()

        if not self._intents:
            # No intents — generate a minimal hello API
            self._intents = ["create a minimal hello world API with a GET / endpoint"]

        print(f"\033[90mGenerating API from {len(self._intents)} intent(s)...\033[0m")
        self._api_source = self._generate_api(self._intents)

        # Save generated source
        self.generated_path.write_text(self._api_source, encoding="utf-8")
        log_transform(self.audit_path, "api_generate", "initial", after=self._api_source)

        # Log intents
        for intent in self._intents:
            self._save_intent_log(intent, source="nx_file")

    def start(self) -> None:
        """Build and start the server."""
        self.build()

        print("\n\033[36m── generated api ──────────────────────────────\033[0m")
        print(self._api_source)
        print("\033[36m───────────────────────────────────────────────\033[0m\n")

        # Load the app
        try:
            self._app = self._load_app_from_source(self._api_source)
            self._app = self._wrap_with_nexus_routes(self._app)
        except Exception as e:
            print(f"\033[31mFailed to load generated API: {e}\033[0m")
            print("\nGenerated source saved to:", self.generated_path)
            raise

        # Print route table
        print("\033[36mRoutes:\033[0m")
        for rule in self._app.url_map.iter_rules():
            if rule.endpoint != "static":
                methods = ",".join(sorted(rule.methods - {"HEAD", "OPTIONS"}))
                print(f"  {methods:8s} {rule.rule}")

        print(f"\n\033[36mManagement:\033[0m")
        print(f"  GET      /_nexus/status     — server status")
        print(f"  POST     /_nexus/intent     — inject new intent")
        print(f"  GET      /_nexus/intents    — list all intents")
        print(f"  GET      /_nexus/source     — view generated code")
        print(f"  GET      /_nexus/describe   — API documentation")
        print(f"  GET      /_nexus/audit      — audit trail")

        print(f"\n\033[32mNexus serving on http://localhost:{self.port}\033[0m")
        print(f"\033[90mCtrl+C to stop. POST to /_nexus/intent to modify at runtime.\033[0m\n")

        # Run with the WSGI dispatcher that supports hot-reload
        self._run_with_reload()

    def _run_with_reload(self) -> None:
        """Run Flask with a WSGI middleware that swaps apps on reload."""
        from werkzeug.serving import make_server

        server_ref = self

        class ReloadableDispatcher:
            """WSGI middleware that delegates to the current app."""
            def __call__(self, environ, start_response):
                return server_ref._app(environ, start_response)

        dispatcher = ReloadableDispatcher()

        try:
            srv = make_server("0.0.0.0", self.port, dispatcher, threaded=True)
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\n\033[90mNexus server stopped.\033[0m")
