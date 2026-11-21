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
| **EC2 capacity reservations** | Bill at the full instance rate whether or not an instance uses them. | `ec2:DescribeCapacityReservations` |
| **EC2 dedicated hosts** | Bill per host-hour while allocated, with or without instances on them. | `ec2:DescribeHosts` |
| **Transfer Family servers** | About $0.30/hour per enabled protocol while the server exists, used or not. | `transfer:ListServers` |
| **DMS replication instances** | Bill per instance-hour while they exist, with or without a running task. | `dms:DescribeReplicationInstances` |
| **Global Accelerator accelerators** | A fixed hourly fee per accelerator, plus traffic. Global, served from `us-west-2`. | `globalaccelerator:ListAccelerators` |
| **Client VPN endpoints** | Bill per subnet association-hour, connected or not. Site-to-Site VPN is covered. | `ec2:DescribeClientVpnEndpoints` |
| **Directory Service directories** | Managed Microsoft AD and AD Connector bill per hour while they exist. | `ds:DescribeDirectories` |
| **Kendra indexes** | From about $800/month for the developer edition, queried or not. | `kendra:ListIndices` |
| **Bedrock provisioned throughput** | Bills per model-unit-hour for the commitment term, invoked or not. | `bedrock:ListProvisionedModelThroughputs` |
| **EMR clusters** | Bill per instance-hour while running; long-running clusters are easy to forget. | `elasticmapreduce:ListClusters` |
| **AWS Batch compute environments** | Free themselves, but a minimum vCPU above zero keeps instances running. | `batch:DescribeComputeEnvironments` |
| **MemoryDB clusters** | Bill per node-hour while they exist, like ElastiCache. | `memorydb:DescribeClusters` |
| **Amazon MQ brokers** | Bill per broker-hour plus storage while they exist. | `mq:ListBrokers` |
| **FSx file systems** | Bill per GB-month of provisioned storage and throughput, mounted or not. | `fsx:DescribeFileSystems` |
| **Storage Gateway gateways** | Volumes, tapes and stored data bill per GB-month. | `storagegateway:ListGateways` |
| **Timestream databases** | Stored data bills per GB-month; LiveAnalytics bills compute while queried. | `timestream:ListDatabases` |
| **Keyspaces tables** | Provisioned capacity bills per hour; storage per GB-month. | `cassandra:Select` (IAM action for `keyspaces:ListTables`) |
| **Athena workgroups** | Free unless provisioned capacity is reserved; queries bill per TB scanned. | `athena:ListWorkGroups` |
| **Route 53 registered domains** | Renew yearly unless auto-renew is turned off. Bills as "Amazon Registrar". | `route53domains:ListDomains` |
| **VPC Lattice service networks** | Bill per service-hour plus requests and data. | `vpc-lattice:ListServiceNetworks` |
| **QuickSight subscription** | A monthly fee per author and reader once the account is subscribed. | `quicksight:DescribeAccountSubscription` |
