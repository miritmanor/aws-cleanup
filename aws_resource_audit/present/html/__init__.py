"""The HTML report: render_html_report substitutes rows, graph payloads and panel
assets into HTML_TEMPLATE (see template.py), plus AWS console deep links."""

import json
import urllib.parse
from html import escape as esc

from ...config import COST_LOOKBACK_DAYS, now
from ...settings import OUTPUT_FILENAMES
from ..cost import (
    BILLING_SECTION_HEADINGS,
    COST_COLUMN_LABEL,
    SECTION_HEADING as COVERAGE_SECTION_HEADING,
    allocation_note,
    below_threshold_note,
    period_note,
    service_entry_lines,
)
from ..bubbles import BUBBLES_CSS, BUBBLES_JS, BUBBLES_PANEL
from ..format import display_row
from ..graph.cytoscape import (
    GRAPH_CSS,
    GRAPH_HEAD,
    GRAPH_JS,
    GRAPH_PANEL,
    GRAPH_SWITCH,
)
from ..graph.diagram import DIAGRAM_CSS, DIAGRAM_HEAD, DIAGRAM_JS, diagram_panel
from ..graph.layout import LAYOUT_JS
from ..graph.mermaid import render_mermaid
from .template import HTML_TEMPLATE, SERVICE_META


def build_console_url(row):
    """Best-effort AWS Console deep link for a resource, or "" when no reliable
    pattern exists."""
    svc, region, rid = row["service"], row.get("region", ""), row["resource_id"]
    q = urllib.parse.quote(rid, safe="")
    if svc == "EC2Instance":
        return f"https://{region}.console.aws.amazon.com/ec2/home?region={region}#InstanceDetails:instanceId={q}"
    if svc == "EBSVolume":
        return f"https://{region}.console.aws.amazon.com/ec2/home?region={region}#VolumeDetails:volumeId={q}"
    if svc == "ElasticIP":
        return f"https://{region}.console.aws.amazon.com/ec2/home?region={region}#Addresses:"
    if svc == "RDSInstance":
        return f"https://{region}.console.aws.amazon.com/rds/home?region={region}#database:id={q};is-cluster=false"
    if svc == "LambdaFunction":
        return f"https://{region}.console.aws.amazon.com/lambda/home?region={region}#/functions/{q}"
    if svc == "LoadBalancer":
        return f"https://{region}.console.aws.amazon.com/ec2/home?region={region}#LoadBalancers:search={q}"
    if svc == "DynamoDBTable":
        return f"https://{region}.console.aws.amazon.com/dynamodbv2/home?region={region}#table?name={q}"
    if svc == "S3Bucket":
        return f"https://s3.console.aws.amazon.com/s3/buckets/{q}"
    if svc == "IAMRole":
        return f"https://us-east-1.console.aws.amazon.com/iam/home#/roles/details/{q}"
    if svc == "IAMUser":
        return f"https://us-east-1.console.aws.amazon.com/iam/home#/users/details/{q}"
    if svc == "IAMPolicy":
        # resource_id is the policy's ARN (see collect_iam) - the IAM console
        # keys its policy detail page off the full ARN, not the policy name.
        return f"https://us-east-1.console.aws.amazon.com/iam/home#/policies/details/{q}?section=permissions"
    if svc == "IAMGroup":
        return f"https://us-east-1.console.aws.amazon.com/iam/home#/groups/details/{q}"
    if svc == "CloudFormationStack":
        return f"https://{region}.console.aws.amazon.com/cloudformation/home?region={region}#/stacks/stackinfo?stackId={q}"
    if svc == "ElasticBeanstalkEnvironment":
        return f"https://{region}.console.aws.amazon.com/elasticbeanstalk/home?region={region}#/environment/dashboard?environmentId={q}"
    if svc == "ElasticBeanstalkApplication":
        return f"https://{region}.console.aws.amazon.com/elasticbeanstalk/home?region={region}#/application/overview?applicationName={q}"
    if svc == "CloudWatchLogGroup":
        return f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#logsV2:log-groups/log-group/{q}"
    if svc == "CloudWatchAlarm":
        return f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#alarmsV2:"
    if svc == "APIGatewayRestApi":
        return f"https://{region}.console.aws.amazon.com/apigateway/main/apis/{q}/resources?region={region}"
    if svc == "APIGatewayV2Api":
        return f"https://{region}.console.aws.amazon.com/apigateway/main/apis/{q}/routes?region={region}"
    if svc == "AmplifyApp":
        return f"https://{region}.console.aws.amazon.com/amplify/home?region={region}#/{q}"
    if svc == "CognitoUserPool":
        return f"https://{region}.console.aws.amazon.com/cognito/v2/idp/user-pools/{q}/users?region={region}"
    if svc == "CloudTrailTrail":
        return f"https://{region}.console.aws.amazon.com/cloudtrail/home?region={region}#/configuration"
    # EC2 hash routes; where no detail page exists, a pre-filtered list page.
    if svc == "LaunchTemplate":
        return f"https://{region}.console.aws.amazon.com/ec2/home?region={region}#LaunchTemplateDetails:launchTemplateId={q}"
    if svc == "AMI":
        return f"https://{region}.console.aws.amazon.com/ec2/home?region={region}#ImageDetails:imageId={q}"
    if svc == "EBSSnapshot":
        return f"https://{region}.console.aws.amazon.com/ec2/home?region={region}#SnapshotDetails:snapshotId={q}"
    if svc == "SecurityGroup":
        return f"https://{region}.console.aws.amazon.com/ec2/home?region={region}#SecurityGroup:groupId={q}"
    if svc == "NetworkInterface":
        return f"https://{region}.console.aws.amazon.com/ec2/home?region={region}#NetworkInterface:networkInterfaceId={q}"
    if svc == "KeyPair":
        return f"https://{region}.console.aws.amazon.com/ec2/home?region={region}#KeyPairs:search={q}"
    if svc == "ReservedInstance":
        return f"https://{region}.console.aws.amazon.com/ec2/home?region={region}#ReservedInstances:search={q}"
    if svc == "SpotInstanceRequest":
        return f"https://{region}.console.aws.amazon.com/ec2/home?region={region}#SpotInstances:search={q}"
    return ""
def render_coverage_banner(coverage):
    """The "what this scan could not see" banner, above the table, or "" if none."""
    gaps = [e for e in (coverage or []) if e.get("status") != "complete"]
    if not gaps:
        return ""
    by_status = {}
    for entry in gaps:
        by_status.setdefault(entry.get("status", "?"), set()).add(
            f"{entry.get('service') or entry.get('collector', '?')}"
            f"{' in ' + entry['scope'] if entry.get('scope') else ''}")
    parts = "".join(
        f"<li><b>{status}</b>: {', '.join(sorted(names)[:12])}"
        f"{' and more' if len(names) > 12 else ''}</li>"
        for status, names in sorted(by_status.items()))
    return (
        '<div class="pill" style="display:block;margin:12px 0;padding:10px 14px;'
        'border-left:4px solid #b7791f">'
        f"<b>{len(gaps)} lookup(s) did not complete.</b> For these, this scan "
        "cannot tell &quot;none exist&quot; from &quot;none were visible&quot;, "
        "so nothing below is proof that a resource is unused."
        f"<ul style=\"margin:6px 0 0 18px\">{parts}</ul></div>")


def render_billing_section(report, cost_report=None):
    """"What you are paying for that this report does not cover", with the same
    headings as the grouping audit."""
    if report is None:
        return ""
    head = ('<details class="pill" style="display:block;margin:12px 0;padding:10px 14px">'
            f"<summary><b>{esc(COVERAGE_SECTION_HEADING)}</b></summary>")
    body = [f"<p>{esc(period_note(report))}</p>"]
    if not report.queried:
        return head + "".join(body) + "</details>"
    currency = esc(report.currency or "USD")
    body.insert(0, f"<p>AWS charged this account <b>{report.total:.2f} "
                   f"{currency}</b>.</p>")
    if not report.has_findings:
        body.append("<p><b>Nothing to report.</b> Every charged service was "
                    "scanned and found.</p>")
    else:
        body.append(f"<p><b>{report.unaccounted:.2f} {currency}</b> of that "
                    "has no resource beside it in this report:</p>")
    for state, heading in BILLING_SECTION_HEADINGS:
        entries = report.in_state(state)
        if not entries:
            continue
        items = []
        for entry in entries:
            said = service_entry_lines(entry, report.currency)
            detail = "".join(f"<li>{esc(line)}</li>" for line in said[1:])
            items.append(f"<li>{esc(said[0])}"
                         + (f"<ul>{detail}</ul>" if detail else "") + "</li>")
        body.append(f"<p><b>{esc(heading)}</b></p><ul>{''.join(items)}</ul>")
    small = below_threshold_note(report)
    if small:
        body.append(f"<p>{esc(small)}</p>")
    allocation = allocation_note(cost_report)
    if allocation:
        body.append(f"<p>{esc(allocation)}</p>")
    return head + "".join(body) + "</details>"


def render_html_report(all_rows, title="AWS Resource Inventory", graph=None,
                       diagram_graph=None, generated_at=None, coverage=None,
                       billing_coverage=None, cost_report=None,
                       overview=None, project_diagrams=()):
    """Render the report to a string (testable without a scan). graph=None omits the
    graph panel; generated_at is when the SCAN ran, not the render."""
    diagram_graph = graph if diagram_graph is None else diagram_graph
    # Only the public schema reaches the browser.
    enriched = [display_row(r, {"console_url": build_console_url(r)})
                for r in all_rows]

    regions = sorted({r["region"] for r in all_rows if r.get("region") and r["region"] != "global"})
    stale_count = sum(1 for r in all_rows if r["flag"].startswith("STALE"))
    high_risk = sum(1 for r in all_rows if r["risk_if_removed"].startswith("HIGH"))
    low_risk = sum(1 for r in all_rows if r["risk_if_removed"].startswith("LOW"))

    return HTML_TEMPLATE.format(
        title=title,
        generated_at=(generated_at or now()).strftime("%Y-%m-%d %H:%M:%S UTC"),
        row_count=len(all_rows),
        region_count=len(regions) if regions else 1,
        stale_count=stale_count,
        high_risk=high_risk,
        low_risk=low_risk,
        coverage_banner=render_coverage_banner(coverage),
        billing_section=render_billing_section(billing_coverage, cost_report),
        # Both the header and the note state the lookback window, not "per month".
        cost_column_label=COST_COLUMN_LABEL,
        cost_lookback_days=COST_LOOKBACK_DAYS,
        rows_json=json.dumps(enriched, default=str),
        service_meta_json=json.dumps(SERVICE_META),
        # overview=None ("bubbles_in_html": false, or a caller that has no
        # summaries) omits the panel entirely rather than drawing an empty one.
        bubbles_css=BUBBLES_CSS if overview else "",
        bubbles_panel=BUBBLES_PANEL if overview else "",
        bubbles_js=BUBBLES_JS if overview else "",
        bubbles_json=json.dumps(overview, default=str) if overview else "null",
        # Bare names: every output lands in one directory, so relative links resolve.
        mermaid_file=OUTPUT_FILENAMES["mermaid"],
        graph_json=json.dumps(graph, default=str) if graph else "null",
        graph_head=GRAPH_HEAD if graph else "",
        graph_css=GRAPH_CSS if graph else "",
        graph_switch=GRAPH_SWITCH if graph else "",
        graph_panel=GRAPH_PANEL if graph else "",
        # LAYOUT_JS and DIAGRAM_JS first: GRAPH_JS is an IIFE that calls them on parse.
        graph_js=(LAYOUT_JS + DIAGRAM_JS + GRAPH_JS) if graph else "",
        # The same string write_mermaid writes to the .mmd, from `diagram_graph`
        # (always contracted, whatever "graph_all_nodes" says).
        diagram_head=DIAGRAM_HEAD if graph else "",
        diagram_css=DIAGRAM_CSS if graph else "",
        diagram_panel=diagram_panel(render_mermaid(diagram_graph, all_rows))
                     if diagram_graph else "",
        # Per-project diagrams from render.py; "" is the whole account, always present.
        diagram_sources_json=json.dumps(
            {"": render_mermaid(diagram_graph, all_rows) if diagram_graph else "",
             **{pid: source for pid, _name, source, _file, _drawio in project_diagrams}}
            if diagram_graph else {}),
        diagram_names_json=json.dumps(
            {pid: name for pid, name, _source, _file, _drawio in project_diagrams}),
        diagram_files_json=json.dumps(
            {"": OUTPUT_FILENAMES["mermaid"],
             **{pid: file for pid, _name, _source, file, _drawio in project_diagrams}}
            if diagram_graph else {}),
        diagram_drawio_files_json=json.dumps(
            {"": OUTPUT_FILENAMES["drawio"],
             **{pid: drawio for pid, _name, _source, _file, drawio in project_diagrams}}
            if diagram_graph else {}),
    )


def generate_html_report(all_rows, html_path, title="AWS Resource Inventory",
                         graph=None, diagram_graph=None, generated_at=None,
                         coverage=None, billing_coverage=None,
                         cost_report=None, overview=None, project_diagrams=()):
    with open(html_path, "w") as f:
        f.write(render_html_report(all_rows, title=title, graph=graph,
                                   diagram_graph=diagram_graph,
                                   generated_at=generated_at, coverage=coverage,
                                   billing_coverage=billing_coverage,
                                   cost_report=cost_report,
                                   overview=overview,
                                   project_diagrams=project_diagrams))
