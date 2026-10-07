"""Re-export of aws_resource_audit/aws_services.py, where the table now lives,
for fetch_service_catalogue.py and the tests."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aws_resource_audit.aws_services import SERVICE_SLUGS, types_by_slug  # noqa: E402,F401
