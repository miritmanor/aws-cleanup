# Service coverage uses the AWS products taxonomy

Status: accepted

## Context

The tool scans 33 resource types and nothing recorded what that is a fraction of.
`ROADMAP.md` tiers unscanned resources by hand-reasoned prose, with no external
reference for what exists or how AWS itself organises it.

Answering "what do we cover" needs two things: a list of AWS services, and a way
to group them into Compute / Storage / Database so the gaps read as something
other than an undifferentiated wall of 300 names.

**The first is available five different ways, and they disagree.** Each list is a
by-product of a different AWS subsystem, and membership is decided by whether a
service team integrated with that subsystem:

| Source | Count | Enumerates |
|---|---|---|
| `boto3.get_available_services()` | 424 | API clients in botocore |
| Pricing `DescribeServices` | 268 | Things that can appear on a bill |
| AWS Config `ResourceType` enum | 533 | Types Config can record history for |
| Resource Explorer | 654 | Types the search index can index |
| CloudFormation registry | 1,720 | Types CloudFormation can provision |

They are not nested and cannot be reconciled by subtraction. Config holds 25
types CloudFormation lacks; CloudFormation holds 1,212 Config lacks. They do not
even agree on names — Config says `AWS::ACM::Certificate` where CloudFormation
says `AWS::CertificateManager::Certificate` — so naive set differences invent
gaps that do not exist. And types this tool already scans appear in none of them:
`AMI` and `EBSSnapshot` have no CloudFormation type because they cannot be
provisioned from a template.

**The second is not available from any AWS API at all.** The CloudFormation
registry, botocore metadata, SSM public parameters, Resource Explorer and the
Pricing API were each checked; none carries a domain category. The
CloudFormation `Category` filter is provenance (AWS vs third-party), not domain.
Pricing's `productFamily` is the closest and is unusable as a taxonomy — it is
billing-shaped and per-service, so all of Lambda is one family called
`Serverless`, SQS returns nothing at all, and EC2 mixes `Compute Instance` with
`IP Address` and `NAT Gateway`.

## Decision

Use the **AWS products page** as the taxonomy, read from the public JSON
directory endpoint that backs it:

```
https://aws.amazon.com/api/dirs/items/search?item.directoryId=aws-products&size=1000&item.locale=en_US
```

It yields 358 entries, 253 of them tagged `Service` rather than `Feature`, each
carrying `productCategory` — `Compute`, `Storage`, `Databases`,
`Networking & Content Delivery`, `Security, Identity, & Compliance` and 21 more.
This is the taxonomy the console and the docs use, and it is the only
AWS-authored domain grouping that exists in machine-readable form.

`tools/fetch_service_catalogue.py` fetches it and writes
`tools/aws_service_coverage.csv`, one row per service. Re-running diffs against
the committed file and reports what AWS added, removed, or recategorised.

Three sub-decisions:

**Rows are services, not resource types.** The category data exists only at
service granularity. Attaching a category to each of the 1,720 CloudFormation
types would require a hand-authored ~293-row namespace→service table — inventing
precisely the classification this decision exists to avoid.

**The join key is the marketing URL path, not the product name.** Names get
rewritten; `https://aws.amazon.com/ec2/` does not. It also matches SSM's
`/aws/service/global-infrastructure/services/<id>/marketingHomeURL`, so the same
key reaches boto3 service ids if that is ever wanted.

**`covered` is binary, beside a count.** "EC2: covered" alone is misleading — the
tool scans 9 EC2 types where the CloudFormation registry lists 89. The count is a
fact; a "partial" verdict would be a judgement.

## Consequences

`SERVICE_SLUGS` in `aws_resource_audit/aws_services.py` (re-exported by
`tools/service_map.py`) is the only hand-authored artefact: each resource type →
its product slug. Two of its entries are genuine judgement calls, commented in
place — EBS is filed under *Storage* as its own product rather than folded into
EC2 under *Compute*, and CloudWatch Logs has no product page so log groups share
the `cloudwatch` slug.

**AWS's filing does not match this tool's architecture, and the CSV reports AWS's
version.** API Gateway lands in *Front-End Web & Mobile*, not Application
Integration; X-Ray lands in *Developer Tools*, away from CloudWatch. Copying the
category through unaltered is the point — the moment these are "corrected" the
file stops being an AWS taxonomy and becomes another hand-maintained opinion.

The endpoint has **no working offset parameter**. Passing `from` makes it answer
`totalHits: 0` rather than an offset page, so the whole catalogue comes in one
~700KB response. A restrictive network shows up as a *truncated read* — HTTP 200
followed by JSON cut off mid-string — rather than a refusal, so the fetcher names
that case explicitly. It also refuses to overwrite the CSV with fewer than 200
services, since a truncated fetch would otherwise read as AWS deleting its
catalogue.

Several directory fields are authored as rich text and arrive as markup:
`freeTierAvailability` is `<p>&nbsp;</p>` for 170 services, meaning "none". These
are stripped to plain text, which changes markup only — the surviving vocabulary
is AWS's wording.

`tests/test_service_map.py` fences all of this offline against the
committed CSV: no network, no credentials. The load-bearing one is that every
slug still matches a real product — when AWS renames a product URL the fetch
still succeeds, the CSV still parses, and the service silently flips to
`covered=no`. Nothing else would notice.

Deliberately **not** included: any coverage percentage. `28/1,720` is arithmetic
about a denominator mostly made of things like `AWS::IoTFleetWise::Campaign`, and
it would be read as a score. The CSV shows which services are covered and lets
that stand.
