"""ACM certificates: public ones are free, so the findings are hygiene - one
nothing uses, one expired, or an imported one about to expire that will not renew."""

from ... import coverage
from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.arns import probable_resource_id_from_arn
from ...collect.calls import paged, safe_call, tags_to_dict
from ...rows import add_edge, error_row, new_row
from ...staleness import days_ago
from ...text import join_nonempty

# Every key type, so ECDSA and RSA-4096 certificates are listed too; the default is RSA-2048 only.
_KEY_TYPES = ["RSA_1024", "RSA_2048", "RSA_3072", "RSA_4096", "EC_prime256v1", "EC_secp384r1", "EC_secp521r1"]
_EXPIRY_WARNING_DAYS = 30


def _flag(cert, users):
    status = cert.get("Status", "")
    if status == "EXPIRED":
        return "STALE (expired)"
    if status != "ISSUED":
        return f"UNKNOWN ({status.lower().replace('_', ' ') or 'status unknown'})"
    if not users:
        return "STALE (not in use by any load balancer, distribution or API)"
    left = -(days_ago(cert.get("NotAfter")) or 0)
    if cert.get("Type") == "IMPORTED" and left <= _EXPIRY_WARNING_DAYS:
        return f"ACTIVE (imported - expires in {left} day(s) and will not renew)"
    return f"ACTIVE (in use by {len(users)} resource(s))"


def collect_acm_certificates(session, region):
    client = session.client("acm", region_name=region, config=RETRY_CONFIG)
    summaries, page_error = paged(client, "list_certificates", "CertificateSummaryList",
                                  service="ACMCertificate", Includes={"keyTypes": _KEY_TYPES})
    if page_error and not summaries:
        return [error_row("ACMCertificate", region, "ERROR", page_error)]
    rows = []
    for summary in summaries:
        arn = summary["CertificateArn"]
        detail = safe_call(client.describe_certificate, CertificateArn=arn)
        cert = summary if "__error__" in detail else detail.get("Certificate", {})
        users = cert.get("InUseBy", [])
        tags = safe_call(client.list_tags_for_certificate, capability=coverage.TAGS, CertificateArn=arn)
        cid = arn.rsplit("/", 1)[-1]
        row = new_row(
            "ACMCertificate", region, cid, cert.get("DomainName") or summary.get("DomainName", cid),
            cert.get("CreatedAt") or cert.get("ImportedAt"), None, None, True, _flag(cert, users),
            "A public ACM certificate is free; private-CA certificates bill monthly. ",
            tags={} if "__error__" in tags else tags_to_dict(tags.get("Tags", [])),
            description=join_nonempty([cert.get("Type", "").lower().replace("_", " "),
                                       f"expires {cert['NotAfter']:%Y-%m-%d}" if cert.get("NotAfter") else "",
                                       f"{len(cert.get('SubjectAlternativeNames', []))} name(s)"], ", "),
            arn=arn)
        raw_capture.record("ACMCertificate", region, cid, cert)
        for user in users:
            target, service = probable_resource_id_from_arn(user)
            if service in ("LoadBalancer", "CloudFrontDistribution", "APIGatewayDomainName"):
                add_edge(row, target, "secures", "certificate InUseBy",
                         conn_type="acmcertificate.any.in-use", target_service=service)
        rows.append(row)
    return rows
