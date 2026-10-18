# Roadmap

AWS resource types the scan does not cover yet and that are planned. How to add
one: [adding-a-resource-type.md](adding-a-resource-type.md).

| Resource | Why | API |
|---|---|---|
| **Lightsail instances** | Separate billing model; bills monthly while it exists. Add only once the bill shows Lightsail. | `lightsail:GetInstances` |
| **WorkSpaces** | Separate billing model; monthly or hourly per desktop. Add only once the bill shows WorkSpaces. | `workspaces:DescribeWorkspaces` |
| **Direct Connect connections** | Port hours bill whether or not traffic flows. Add only once the bill shows Direct Connect. | `directconnect:DescribeConnections` |
| **SageMaker processing jobs** | Studio apps and training jobs are covered; processing jobs bill per instance-hour the same way. | `sagemaker:ListProcessingJobs` |
| **AppSync Event APIs** | GraphQL APIs are covered; Event APIs use a separate API and are not listed. | `appsync:ListApis` |
