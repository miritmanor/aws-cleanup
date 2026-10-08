"""The icon exists twice: in the CLI report (brand.py) and as the web app's favicon.
This keeps the two copies identical."""

import unittest
from pathlib import Path

from aws_resource_audit.present.html.brand import FAVICON_SVG

WEB_FAVICON = Path(__file__).resolve().parent.parent / "frontend" / "public" / "favicon.svg"


@unittest.skipUnless(WEB_FAVICON.exists(), "no frontend/ here (the backend Docker image)")
class FaviconTests(unittest.TestCase):
    def test_web_app_favicon_matches_the_report_icon(self):
        """Edit brand.py and copy FAVICON_SVG into frontend/public/favicon.svg."""
        self.assertEqual(WEB_FAVICON.read_text(), FAVICON_SVG)
