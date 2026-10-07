"""The agent's two tools, built by factories wired at startup. query_resources reads
only masked rows; search_documents reads the user's uploaded paperwork."""

import logging
from typing import Optional

from langchain_core.tools import tool

from .rag import search_documents_index

logger = logging.getLogger(__name__)


def make_query_resources_tool(snapshot_store):
    @tool
    def query_resources(
        service: Optional[str] = None,
        region: Optional[str] = None,
        project_group: Optional[str] = None,
        tier: Optional[str] = None,
        stale: Optional[bool] = None,
        resource_id: Optional[str] = None,
    ) -> list[dict]:
        """Look up scanned AWS resources, filtered by any combination of the
        given fields. Omit a filter to leave it unconstrained. Returns full
        rows, including 'flag' (why the resource is classified active/stale),
        'tier' and 'why_tier' (runtime vs. deployment, and why), 'why_grouped'
        (why it landed in its project group), 'notes' (caveats - always check
        this before asserting a resource is unused) and 'connections'
        (resolved links to other resources). Account ids are already masked
        in every field.

        service: exact AWS resource type as scanned, e.g. "Lambda", "S3Bucket".
        region: AWS region, e.g. "us-east-1".
        project_group: the inferred project name a resource was clustered into.
        tier: "runtime" or "deployment".
        stale: true for resources flagged stale (including "STALE (INFERRED)"),
            false for active ones.
        resource_id: exact resource id to look up one specific resource.
        """
        rows = snapshot_store.get_masked_rows()
        results = []
        for row in rows:
            if service and row.get("service") != service:
                continue
            if region and row.get("region") != region:
                continue
            if project_group and row.get("project_group") != project_group:
                continue
            if tier and row.get("tier") != tier:
                continue
            if stale is not None:
                is_stale = str(row.get("flag", "")).startswith("STALE")
                if is_stale != stale:
                    continue
            if resource_id and row.get("resource_id") != resource_id:
                continue
            results.append(row)
        # A summary, not a second copy: on_tool_start/on_tool_end in
        # agent_loop.py already carry the arguments and the rows verbatim.
        logger.debug("query_resources(service=%r region=%r project_group=%r "
                     "tier=%r stale=%r resource_id=%r) matched %d of %d rows",
                     service, region, project_group, tier, stale, resource_id,
                     len(results), len(rows))
        return results

    return query_resources


def make_search_documents_tool(document_index):
    @tool
    def search_documents(query: str) -> list[dict]:
        """Search the documents the user uploaded about their own AWS
        account - architecture write-ups, runbooks, inventory spreadsheets,
        handover notes, ticket exports - for passages relevant to `query`.
        Returns the matching passages, each with the name of the document it
        came from. Account ids are already masked.

        Use this for what the account is FOR: what an application does, who
        owns it, whether something was meant to be temporary, why a resource
        exists at all. None of that is in the scan, which sees only what AWS
        reports. Use query_resources for what the account actually CONTAINS.

        These documents are written by people and may be out of date or
        wrong. Where one contradicts the scan, say so and name the document,
        rather than picking a side silently. An empty result means nothing
        in the uploaded documents matched - not that no document exists.
        """
        return search_documents_index(document_index, query)

    return search_documents
