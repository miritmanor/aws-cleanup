# Deploying the web app to AWS (ECS)

Runs the web app on AWS Fargate in your own account. Bring it up when you need
it and take it down afterwards. It has no public address: you open it through
an encrypted tunnel at `http://127.0.0.1:8080` on your own machine. Every
resource is tagged `Project=aws-cleanup`. The agent is not included.

Two stacks:

- **`aws-cleanup-base`** holds the data (last scan, saved project names,
  merges, settings). Create it once and keep it.
- **`aws-cleanup-app`** runs the app. Create it to bring the app up, delete it
  to take it down.

## Cost

| State | Cost |
|---|---|
| Up | about $0.025 an hour (about $18 a month if left up) |
| Down (base stack only) | a few cents a month |
| Removed | nothing |

Each scan also makes one or two Cost Explorer requests at $0.01 each.

## What you need

- **To create the stacks:** an AWS identity that can create CloudFormation
  stacks, IAM roles, a VPC, ECS, EFS and CloudWatch Logs resources (and ECR
  repositories when building from source). In practice, an administrator.
- **To connect:** `bash` (macOS, Linux, or Windows with WSL),
  [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
  and the
  [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)
  (macOS: `brew install --cask session-manager-plugin`).
- **To build from source,** also: Docker with Compose, git, Python 3.9+.

## Option A: from a release (no clone, no build)

1. From the [latest release](https://github.com/miritmanor/aws-cleanup/releases/latest),
   download `aws-cleanup-base.yaml`, `aws-cleanup-app.yaml`, `ecs-connect.sh`
   and `SHA256SUMS`. Check them: `shasum -a 256 -c SHA256SUMS`.

2. **Create the base stack** (once). In the
   [CloudFormation console](https://console.aws.amazon.com/cloudformation/),
   in the region you want: **Create stack → With new resources → Upload a
   template file** → `aws-cleanup-base.yaml`. Stack name `aws-cleanup-base`;
   keep the parameter defaults. When it is created: **Stack actions → Edit
   termination protection → Activated**, so it cannot be deleted by mistake.

   Or with the CLI:
   ```bash
   aws cloudformation deploy --stack-name aws-cleanup-base \
     --template-file aws-cleanup-base.yaml --tags Project=aws-cleanup
   aws cloudformation update-termination-protection \
     --enable-termination-protection --stack-name aws-cleanup-base
   ```

3. **Bring the app up.** Create a stack from `aws-cleanup-app.yaml`, named
   `aws-cleanup-app`, with the parameter defaults. Tick "I acknowledge that
   AWS CloudFormation might create IAM resources". It takes about five
   minutes.

   Or with the CLI:
   ```bash
   aws cloudformation deploy --stack-name aws-cleanup-app \
     --template-file aws-cleanup-app.yaml --capabilities CAPABILITY_IAM \
     --tags Project=aws-cleanup
   ```

4. **Connect:**
   ```bash
   chmod +x ecs-connect.sh
   AWS_REGION=<the region> ./ecs-connect.sh
   ```
   Open `http://127.0.0.1:8080`. Ctrl-C closes the tunnel; the app keeps
   running. Without the script: the app stack's **Outputs** tab lists the
   three AWS CLI commands it runs.

5. **Take it down:** delete the `aws-cleanup-app` stack. The data stays in
   the base stack. To bring it back, repeat step 3.

**Updating to a new release:** update the `aws-cleanup-app` stack with the
new `aws-cleanup-app.yaml` (console: **Update → Replace existing template**;
CLI: the same `deploy` command). Update the base stack only when the release
notes say so.

**Checking an image** (optional, needs the [GitHub CLI](https://cli.github.com/)):
the app template's `BackendImage` and `FrontendImage` defaults name the
images. This confirms an image was built by this repository's release
workflow:
```bash
gh attestation verify oci://<image> --repo miritmanor/aws-cleanup
```

## Option B: from source

Builds the images on your machine from the code you cloned, and pushes them
to repositories in your own account.

```bash
git clone https://github.com/miritmanor/aws-cleanup.git
cd aws-cleanup
AWS_PROFILE=<profile> AWS_REGION=<region> deploy/ecs/ecs-up.sh
```

The first run takes several minutes: the backend build runs the test suite.
The images are built for your Docker engine's CPU type (Apple Silicon: ARM64),
and the task is set to match. Run `ecs-up.sh` again after pulling new code to
update the app.

| Script | Does |
|---|---|
| `deploy/ecs/ecs-up.sh` | Creates or updates both stacks; builds and pushes images when this commit has none yet |
| `deploy/ecs/ecs-connect.sh` | Opens the tunnel to `http://127.0.0.1:8080` |
| `deploy/ecs/ecs-down.sh` | Takes the app down; the data stays |
| `deploy/ecs/ecs-remove-all.sh` | Removes both stacks and the data; asks first |

## Settings

Environment variables, all optional, read by the scripts:

| Variable | Meaning | Default |
|---|---|---|
| `AWS_PROFILE`, `AWS_REGION` | The account and region the app runs in. Not what it scans: it scans every region. | AWS CLI defaults |
| `AUDIT_APP_NAME` | Prefix for the stacks and resources, and the `Project` tag. Lets two deployments share an account. In the console, use it as the stack names' prefix and the `AppName` parameter of both stacks. | `aws-cleanup` |
| `AUDIT_LOCAL_PORT` | The port `ecs-connect.sh` opens on your machine | `8080` |

## Remove everything

**This deletes the data: the last scan, saved project names, merges and
settings. There is no undo, and no built-in way yet to export them first.**

- From source: `deploy/ecs/ecs-remove-all.sh`. It lists what it will delete
  and asks you to type the app name.
- In the console: delete `aws-cleanup-app`, then on `aws-cleanup-base`
  **Stack actions → Edit termination protection → Deactivated**, then delete
  it.

## What gets created

| Stack | Resources |
|---|---|
| base | An encrypted EFS file system and access point; from source, two ECR repositories |
| app | A VPC with two public subnets and no NAT gateway; security groups; an ECS cluster, task definition and service (one Fargate task, 0.5 vCPU, 1 GB); two IAM roles; a CloudWatch log group (7 days) |

## Security

- **Nothing accepts connections from the internet.** The task has a public IP
  only to make outgoing calls (AWS APIs in every region, the image registry,
  logs); its security group allows nothing in.
- **Who can open the app:** anyone in the account allowed to start a Session
  Manager session on the task. The app has no login of its own, as with local
  Docker. To let someone connect without being able to deploy, give them:
  ```json
  {
    "Version": "2012-10-17",
    "Statement": [
      {"Effect": "Allow", "Action": ["ecs:ListTasks", "ecs:DescribeTasks", "sts:GetCallerIdentity"], "Resource": "*"},
      {"Effect": "Allow", "Action": ["ssm:StartSession", "ecs:ExecuteCommand"], "Resource": [
        "arn:aws:ecs:<region>:<account>:task/aws-cleanup/*",
        "arn:aws:ecs:<region>:<account>:cluster/aws-cleanup",
        "arn:aws:ssm:<region>::document/AWS-StartPortForwardingSession"]}
    ]
  }
  ```
- **What the app can do in your account:** read, with exactly the actions in
  [`iam/aws-audit-role.yaml`](../../iam/aws-audit-role.yaml), plus what the
  tunnel and the data mount need. Nothing that creates, changes or deletes.

## Troubleshooting

| Message or symptom | What to do |
|---|---|
| `No app 'aws-cleanup' in <region>` | Wrong region or app name (`AWS_REGION`, `AUDIT_APP_NAME`), or the app stack is not up. |
| `The task is starting` | Wait a minute and run `ecs-connect.sh` again. |
| `SessionManagerPlugin is not found` / `session-manager-plugin not found` | Install the plugin (see What you need). |
| App stack fails with `No export named aws-cleanup-FileSystemId` | Create the base stack first, in the same region, with the same `AppName`. |
| App stack stuck creating the service, then rolls back | The task keeps stopping. Look in the CloudWatch log group `/ecs/aws-cleanup`, and in the ECS console under the cluster's stopped tasks. |
| First ECS use in the account fails on a service-linked role | Your identity needs `iam:CreateServiceLinkedRole`; ECS creates the role once. |
| Base stack will not delete | Termination protection is on (deliberately), or the app stack still exists. |
