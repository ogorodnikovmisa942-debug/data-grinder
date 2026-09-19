#!/usr/bin/env python3
"""
CLI script to bundle or split frontend JavaScript modules.
Usage:
    python scripts/build_frontend.py          # Bundles modules into app.js
    python scripts/build_frontend.py --split  # Splits app.js into modules
"""

import sys
import os

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.frontend_bundler import bundle_modules, split_app_js

if __name__ == "__main__":
    if "--split" in sys.argv:
        split_app_js()
        print("Frontend modules generated in app/static/js/modules/")
    else:
        bundle_modules()
        print("Frontend modules bundled successfully into app/static/js/app.js")
