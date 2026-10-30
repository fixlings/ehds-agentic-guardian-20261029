"""Local live API for an existing frontend.

Start with the key already in the process environment:

    .\\.venv\\Scripts\\python.exe -m guardian.api.live

The key is read only by the EU Mistral client inside this process.
It is not a request field and is not written to logs here.
"""

from __future__ import annotations

import os

HOST = "127.0.0.1"
PORT = 8000


def main() -> None:
    if not os.environ.get("MISTRAL_API_KEY"):
        raise SystemExit("MISTRAL_API_KEY is not set. Live API was not started.")
    try:
        from guardian.api.app import create_app

        app = create_app(use_env_model=True)
    except Exception:
        raise SystemExit("EU Mistral client could not be created. Live API was not started.") from None
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
