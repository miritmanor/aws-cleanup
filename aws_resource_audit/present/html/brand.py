"""The product name, tagline and icon. frontend/public/favicon.svg must equal
FAVICON_SVG; tests/test_brand.py checks it."""

import base64

APP_NAME = "AWS Cleanup"
TAGLINE = "see everything in your account, remove what you don't need"

FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="14" fill="#232F3E"/>
  <circle cx="28" cy="28" r="14" fill="none" stroke="#FF9900" stroke-width="5"/>
  <path d="M38.5 38.5 50 50" stroke="#FF9900" stroke-width="6" stroke-linecap="round"/>
  <path d="M21 28.5l5 5 9-10" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""

FAVICON_DATA_URI = "data:image/svg+xml;base64," + base64.b64encode(FAVICON_SVG.encode()).decode()
