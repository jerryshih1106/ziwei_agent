import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")
