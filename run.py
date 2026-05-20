import sys
import os
import traceback

project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)
os.chdir(project_root)

# Print real import error instead of uvicorn's generic message
try:
    import main as _main_test  # noqa: F401
except Exception:
    print("=" * 60, flush=True)
    print("STARTUP IMPORT ERROR — real traceback:", flush=True)
    traceback.print_exc()
    print("=" * 60, flush=True)
    sys.exit(1)

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")
