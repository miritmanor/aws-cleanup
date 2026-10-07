"""Runtime or deployment: which half of a project a resource is, as an axis separate
from project_group. Rules read AWS-stated evidence; no evidence defaults to runtime."""

import re

TIER_RUNTIME = "runtime"
TIER_DEPLOYMENT = "deployment"

# Fixed CloudFormation logical ids the Amplify CLI emits. Kept SHORT: not a name heuristic.
DEPLOYMENT_LOGICAL_IDS = {
    "deploymentbucket",
    # The custom resources that run during a push to finish wiring auth up:
    # they exist to complete a deployment and are never invoked by the app.
    "userpoolclientlambda",
    "updateroleswithidpfunction",
    "rolemapfunction",
}

# Amplify CLI category stacks. Everything the application is made of lives in
# one of these; the root stack (category "") holds what deploys them.
AMPLIFY_APPLICATION_CATEGORIES = {"api", "function", "storage", "auth", "hosting"}

# Types that are provisioning machinery wherever they sit, each with its own reason.
DEPLOYMENT_SERVICES = {
    "CloudFormationStack":
        "a CloudFormation stack is how resources came to exist, not something "
        "the application calls",
    "ElasticBeanstalkApplication":
        "an Elastic Beanstalk application is the version container an "
        "environment is deployed from, not the environment that serves requests",
    "ECRRepository":
        "a container registry holds the images a deployment pulls, not the service that runs them",
    "CodeBuildProject": "a build project produces what gets deployed; it is not what runs",
    "CodePipeline": "a pipeline is how changes reach the application, not part of it",
}

# Connection types whose TARGET is on a request path, by the nature of the mechanism.
RUNTIME_TARGET_CONN_TYPES = {
    "apigateway.lambda.integration",
    "apigateway.lambda.stage-variable",
    "apigatewayv2.lambda.integration",
    "cognito.lambda.trigger",
    "sns.any.subscription",
    "sqs.sqs.redrive-policy",
    "lambda.any.dead-letter-config",
    "amplify.apigateway.env-var-execute-api-url",
    "lambda.apigateway.env-var-execute-api-url",
}


# Connection types whose TARGET is a bucket the source deploys from. Read off resolved
# references, so switching the link off also stops the rule.
DEPLOYMENT_BUCKET_CONN_TYPES = {
    "ebapplication.s3bucket.source-bundle": "Elastic Beanstalk",
    "ebapplication.s3bucket.service-bucket": "Elastic Beanstalk",
    "elasticbeanstalk.s3bucket.deployment-artifact": "Elastic Beanstalk",
    "codepipeline.s3bucket.artifact-store": "CodePipeline",
}


# Bucket names AWS generates for itself, in a documented format no person would choose.
# The one rule reading a name; it fires last. Kept SHORT.
AWS_MANAGED_BUCKET_NAMES = (
    (re.compile(r"^cf-templates-[0-9a-z]{8,}-[a-z]{2}(?:-[a-z]+)+-\d$"),
     "CloudFormation generated this bucket to stage templates uploaded to it - "
     "it holds copies of templates already applied, not anything the "
     "application reads"),
)


def _deployment_evidence(all_rows):
    """The facts the rules read, as flat lookups. `artifacts` maps a bucket to (which
    service said so, which application)."""
    provenance, artifacts = {}, {}
    for row in all_rows:
        app = row.get("name") or row.get("resource_id")
        for pid, record in (row.get("_amplify_provenance") or {}).items():
            provenance.setdefault(pid, record)
        for bucket in row.get("_amplify_deployment_artifacts") or ():
            artifacts.setdefault(bucket, ("Amplify", app))
        for ref in row.get("_references") or ():
            label = DEPLOYMENT_BUCKET_CONN_TYPES.get(ref.get("conn_type"))
            if label and ref.get("kind") == "resolved":
                artifacts.setdefault(ref["target_id"], (label, app))
    return provenance, artifacts


def _is_runtime_target(row):
    """Does a request-path link resolve to this row? Read from incoming references,
    so a dangling reference classifies nothing."""
    return any(ref.get("kind") == "incoming"
               and ref.get("conn_type") in RUNTIME_TARGET_CONN_TYPES
               for ref in row.get("_references") or ())


def _aws_managed_bucket(row):
    """The reason, if this row is a bucket AWS named for its own use. S3Bucket only."""
    if row.get("service") != "S3Bucket":
        return None
    name = row.get("resource_id") or ""
    for pattern, reason in AWS_MANAGED_BUCKET_NAMES:
        if pattern.match(name):
            return reason
    return None


# Empty: a default verdict has no reason worth reading.
DEFAULT_WHY_TIER = ""


def _classify(row, provenance, artifacts):
    """(tier, why) for one row: strongest evidence first, first hit wins. Defaults to
    (TIER_RUNTIME, DEFAULT_WHY_TIER)."""
    rid = row.get("resource_id", "")

    if rid in artifacts:
        label, app = artifacts[rid]
        return (TIER_DEPLOYMENT,
                f"{label} names this bucket as the deployment artifact store for "
                f"{app}")

    service_reason = DEPLOYMENT_SERVICES.get(row.get("service"))
    if service_reason:
        return (TIER_DEPLOYMENT, service_reason)

    record = provenance.get(rid)
    if record:
        logical, category = record.get("logical_id", ""), record.get("category", "")
        if logical.lower() in DEPLOYMENT_LOGICAL_IDS:
            return (TIER_DEPLOYMENT,
                    f"CloudFormation logical id '{logical}' - provisioning machinery, "
                    "not part of the running application")
        if category in AMPLIFY_APPLICATION_CATEGORIES:
            return (TIER_RUNTIME,
                    f"provisioned by this app's Amplify '{category}' category stack")
        if not category:
            return (TIER_DEPLOYMENT,
                    f"sits directly in the backend root stack (as '{logical}'), which "
                    "deploys the category stacks rather than serving requests")

    # Last of the deployment rules on purpose: a name, even one AWS wrote, is
    # weaker than anything AWS stated, so every rule above can overrule it.
    managed = _aws_managed_bucket(row)
    if managed:
        return (TIER_DEPLOYMENT, managed)

    if _is_runtime_target(row):
        return (TIER_RUNTIME, "on a request path - something serving traffic links to it")

    return (TIER_RUNTIME, DEFAULT_WHY_TIER)


def apply_tiers(all_rows):
    """Stamp `tier` and `why_tier` on every row; returns {tier: count}. Must run AFTER
    resolve_edges."""
    provenance, artifacts = _deployment_evidence(all_rows)

    counts = {}
    for row in all_rows:
        tier, why = _classify(row, provenance, artifacts)
        row["tier"], row["why_tier"] = tier, why
        if tier:
            counts[tier] = counts.get(tier, 0) + 1
    return counts
