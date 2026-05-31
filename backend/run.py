import sys
import os

# 1. Prioritize backend root first to prevent shadowing
_BACKEND_ROOT = os.path.abspath(os.path.dirname(__file__))
while _BACKEND_ROOT in sys.path:
    sys.path.remove(_BACKEND_ROOT)
sys.path.insert(0, _BACKEND_ROOT)

# 2. Add project root to the very end of sys.path
_PROJECT_ROOT = os.path.abspath(os.path.join(_BACKEND_ROOT, ".."))
while _PROJECT_ROOT in sys.path:
    sys.path.remove(_PROJECT_ROOT)
sys.path.append(_PROJECT_ROOT)

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
