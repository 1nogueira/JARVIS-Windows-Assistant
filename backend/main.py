from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "backend.api.app:app",
        host=os.getenv("JARVIS_HOST", "127.0.0.1"),
        port=int(os.getenv("JARVIS_PORT", "8742")),
        reload=os.getenv("JARVIS_RELOAD", "0") == "1",
        log_level=os.getenv("JARVIS_LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
