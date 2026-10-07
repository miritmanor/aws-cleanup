"""What removing a resource would cost. Runs after edge resolution, so "has
connections" means "still points at something in this scan"."""



def compute_risk(flag, has_connections, extra_hint=None, has_dangling=False,
                 evidence_gap=""):
    """Very rough triage heuristic. NOT a delete/keep verdict - always check
    actual dependents (including services this script doesn't scan)."""
    if flag == "ERROR" or flag == "UNKNOWN":
        return "UNKNOWN - insufficient signal, verify manually"
    active = flag.startswith("ACTIVE")
    if extra_hint == "unattached":
        return "LOW - unattached/unassociated, common cleanup candidate (still verify it's not a standby/DR resource)"
    if extra_hint == "attached_idle":
        # Costing money, but still attached to something kept; the stop may be deliberate.
        return ("MEDIUM - attached to a STOPPED instance: billed while nothing reads it, "
                "so it is a cost to look at, but the data is still attached to something "
                "and the instance may be stopped deliberately. Check before deleting.")
    if has_dangling and not has_connections:
        if evidence_gap:
            # Unresolved references with a gap in the evidence: missing evidence is not evidence.
            return (f"UNKNOWN - its only references point at resources not found in this "
                    f"scan, and {evidence_gap}; resolve that before concluding anything "
                    "about removing this")
        return ("LOW - its only references point at resources confirmed absent, so nothing "
                "found still depends on it (verify anything outside this scan's scope "
                "before removing)")
    if active and has_connections:
        return "HIGH - active and wired to other resources; removing will likely break something"
    if active and not has_connections:
        return "MEDIUM - shows activity but no connections detected in this scan; confirm what's calling it first"
    if not active and has_connections:
        return "MEDIUM - stale but still wired to other resources; check dependents before removing"
    return "LOW - stale with no connections detected; good cleanup candidate (still verify manually)"
