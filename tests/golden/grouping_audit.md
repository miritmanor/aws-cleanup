# Grouping audit

103 resources scanned, 2 project(s).

## Scan coverage

1 of 4 lookup(s) did not complete. Where a resource type appears here, this scan cannot tell "none exist" from "none were visible", and nothing below should be read as proof that something is unused.

| Scope | Resource type | What | Status | Why |
|---|---|---|---|---|
| us-east-1 | DynamoDBTable | inventory | denied | AccessDenied: dynamodb:ListTables |

- **denied**: grant the action named in the error (iam/aws-audit-role.yaml)

## What you are paying for that this report does not cover

AWS charged this account **885.44 USD**. Anything listed below is money with no resource beside it in this report.

Recorded spend for 2026-05-02 to 2026-06-01, in USD, observed 2026-06-01 12:00:00 UTC.

**15.34 USD of that is listed below.**

### Charged for, and this tool does not scan it at all (1)

- **Amazon Lex** - 12.34 USD

### Charged for, and the scan was not allowed to look (1)

Resources you pay for may be missing. Grant the permission (iam/aws-audit-role.yaml) and scan again.

- **Amazon DynamoDB** - 3.00 USD
  - this report holds 2 DynamoDBTable
  - DynamoDBTable lookup: denied
  - AWS also bills backup and restore storage here, which this tool does not collect

### Accounted for

Scanned and found.

- **Amazon Elastic Compute Cloud - Compute** - 825.10 USD; this report holds 4 EC2Instance, 1 ReservedInstance, 1 SpotInstanceRequest
- **EC2 - Other** - 40.00 USD; this report holds 1 AMI, 1 EBSSnapshot, 1 EBSVolume, 1 ElasticIP, 1 NatGateway
  - AWS splits the charge as: NAT gateway 31.00; EBS volume storage 9.00 - all of it scanned, so no sign of inter-AZ and internet data transfer here

**Not resources**: Tax (5.00) - tax, support and credits. Nothing to scan.

## Why some resources show no cost

These resources are in the table above. The charges below are theirs together, with no defensible way to say how much belongs to which, so their cost column is blank.

- **EC2 - Other in us-east-1**: 40.00 USD - this bill also covers inter-AZ and internet data transfer, which this scan does not collect, so any share would include money that is not theirs
- **Amazon DynamoDB in us-east-1**: 3.00 USD - the scan was refused or cut short on DynamoDBTable in us-east-1, so it may not have found everything this charge covers
- rounding: 0.04 USD left over from the buckets that were divided - shares are rounded down so they can never exceed the bill

Of the 885.44 USD, 825.06 is placed on individual resources and 60.38 is not.

## Coincidental id/name collisions caught and excluded (1)

resolve_edges found a same-id/name match of the WRONG resource type and refused to link it - see each row's connections column for exactly what it matched instead.

- EC2Instance:i-0collision000001

## Dangling references (1)

These resources reference something not found in this scan (deleted, or outside the scanned regions/account) - see connections for what's missing.

- LambdaFunction:orders-worker

## Names that could group things (6)

Words appearing in two or more resource names where nothing else connects those resources - so writing a rule for one would actually change the grouping. Nothing here is applied: add a word to "name_rules" in the groups file to act on it.

- **capacity** - 2 resources: ReservedInstance:ri-0baseline000001, SpotInstanceRequest:sir-0baseline0001
- **directory** - 2 resources: CognitoUserPool:us-east-1_orders01, CognitoUserPool:us-east-1_pool0001
- **jenkins** - 3 resources: EBSVolume:vol-0baseline000001, EC2Instance:i-0baseline0000001, NetworkInterface:eni-0baseline00001
- **public** - 2 resources: APIGatewayRestApi:restapi0001, APIGatewayV2Api:httpapi0001
- **weekly** - 2 resources: EBSSnapshot:snap-0baseline00001, EventBridgeSchedule:weekly-cleanup
- **worker** - 3 resources: CloudWatchLogGroup:/aws/lambda/orders-worker, LambdaFunction:orders-worker, LaunchTemplate:lt-0baseline000001

