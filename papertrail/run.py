import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.getenv("PAPERTRAIL_HOST", "127.0.0.1"),
        port=int(os.getenv("PAPERTRAIL_PORT", "8600")),
        log_level="info",
    )
