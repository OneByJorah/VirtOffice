#!/usr/bin/env python3
"""Capture a screenshot of the VirtOffice dashboard."""
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

repo = Path(__file__).resolve().parents[1]
url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9502"
out = repo / "docs" / "screenshots" / "dashboard.png"
out.parent.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(url, wait_until="networkidle")
    time.sleep(2)
    page.screenshot(path=str(out), full_page=False)
    browser.close()

print(f"Screenshot saved to {out}")
