# 0073 - the audit role names exact actions, checked against the code

Status: accepted.

## Context

`iam/aws-audit-role.yaml` granted wildcards: `ec2:Describe*`,
`elasticloadbalancing:Describe*`, `rds:Describe*`, `lambda:List*`/`Get*`,
`s3:List*`/`GetBucket*`, `iam:List*`/`Get*`, `dynamodb:List*`/`Describe*` and
`amplify:List*`. These are reads, but broader than the code needs, and a
reader cannot check a wildcard against the code. Nothing checked the list
either: three granted actions (`cloudtrail:ListTrails`,
`cloudwatch:ListMetrics`, `iam:GenerateCredentialReport`) were never called.

## Decision

The role lists every action by name. `tests/test_iam_role.py` reads every
operation name in `aws_resource_audit/collect/` with `ast`, resolves it to an
IAM action through botocore's own service models (the service is decided by
the clients the file creates), and fails if:

- a call is not granted,
- a grant is not called,
- an action contains `*`,
- an action's verb is not `Describe`, `List`, `Get` or `BatchGet`
  (`apigateway:GET` is the one exception: that is how IAM names API Gateway
  reads).

Where IAM's action name differs from the API's, the test holds the mapping
(`ListBuckets` is `s3:ListAllMyBuckets`, `ListObjectsV2` is `s3:ListBucket`,
`GetBucketNotificationConfiguration` is `s3:GetBucketNotification`).

## Consequences

- The AGENTS.md rule "a new collector must grant its action" is now enforced
  rather than remembered.
- `lambda:GetFunction` stays, with a comment in the role: it is called only
  for container-image functions, to read the image URI. AWS returns a link to
  download a zip function's code in the same response; the tool never
  follows it.
- `Resource: '*'` stays: an inventory cannot name the ARNs it has not found
  yet.
- The test resolves names statically, so it can be fooled by an operation
  name built at run time from strings. Nothing in `collect/` does that today.
