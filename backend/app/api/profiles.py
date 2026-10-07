"""Profile names suggested by the Scan panel, read from the shared credentials file
with the core's own parser. No AWS calls."""

import os

from fastapi import APIRouter

from aws_resource_audit.collect.credentials import credentials_file, profiles_in

from ..schemas import ProfilesResponse

router = APIRouter(prefix="/api", tags=["profiles"])


@router.get(
    "/profiles",
    response_model=ProfilesResponse,
    summary="Profile names defined in the credentials file",
)
def get_profiles():
    """Suggestions only. The config file is not read: it is not mounted in the container."""
    path = credentials_file()
    return ProfilesResponse(
        profiles=profiles_in(path),
        path=path,
        exists=os.path.exists(path),
    )
