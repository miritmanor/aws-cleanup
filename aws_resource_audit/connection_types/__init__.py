"""Every link this scan can infer, declared once per service family.
registry.py holds the on / report-only / off state of each."""

from . import compute
from . import data
from . import network
from . import integration
from . import security
from . import deployment
from . import grouping

CONNECTION_TYPES = (
    *compute.TYPES,
    *data.TYPES,
    *network.TYPES,
    *integration.TYPES,
    *security.TYPES,
    *deployment.TYPES,
    *grouping.TYPES,
)
