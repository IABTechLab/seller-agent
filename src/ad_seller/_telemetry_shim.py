"""Telemetry / shutdown shim for crewai 1.14.6 (ar-r82f.21).

Sets opt-out env vars *before* any crewai / chromadb / posthog / opentelemetry
import path runs, so their constructors take the disabled branch instead of
registering daemon threads and atexit handlers that hang the interpreter for
~5 minutes on shutdown.

This module must be imported BEFORE crewai. The `ad_seller/__init__.py` imports
it as the very first statement.

CLI entrypoints additionally call `os._exit(0)` after `app()` returns to bypass
any straggling atexit handlers that snuck in via transitive deps.
"""

from __future__ import annotations

import os

_TELEMETRY_ENV = {
    "OTEL_SDK_DISABLED": "true",
    "CREWAI_DISABLE_TELEMETRY": "true",
    "CREWAI_DISABLE_TRACKING": "true",
    "CREWAI_TELEMETRY_OPT_OUT": "true",
    "ANONYMIZED_TELEMETRY": "false",
    "POSTHOG_DISABLED": "true",
    "CHROMA_TELEMETRY_DISABLED": "true",
    "DO_NOT_TRACK": "1",
}

for _k, _v in _TELEMETRY_ENV.items():
    os.environ.setdefault(_k, _v)


def force_exit_after(callable_):
    """Wrap a typer/click entrypoint so the process hard-exits on return.

    Usage in CLI main:
        if __name__ == "__main__":
            force_exit_after(app)()
    """

    def _wrapped(*args, **kwargs):
        try:
            result = callable_(*args, **kwargs)
            os._exit(0)
            return result
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 0
            os._exit(code)

    return _wrapped
