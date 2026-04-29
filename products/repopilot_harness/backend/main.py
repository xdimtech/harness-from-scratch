from __future__ import annotations

import uvicorn

from app.api import create_app

app = create_app()


def main() -> None:
    uvicorn.run("main:app", host="127.0.0.1", port=8877, reload=False)


if __name__ == "__main__":
    main()
