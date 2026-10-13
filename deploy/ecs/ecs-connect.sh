#!/usr/bin/env bash
# Open the ECS-deployed web app at http://127.0.0.1:<port> through an SSM tunnel. Standalone.
# Settings: AWS_PROFILE, AWS_REGION, AUDIT_APP_NAME (aws-cleanup), AUDIT_LOCAL_PORT (8080).

set -euo pipefail

APP_NAME="${AUDIT_APP_NAME:-aws-cleanup}"
PORT="${AUDIT_LOCAL_PORT:-8080}"

die() {
    echo "error: $*" >&2
    exit 1
}

command -v aws >/dev/null 2>&1 \
    || die "aws not found. Install AWS CLI v2: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
command -v session-manager-plugin >/dev/null 2>&1 \
    || die "session-manager-plugin not found. Install it: https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html"

AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || true)}}"
[ -n "$AWS_REGION" ] || die "No AWS region. Set AWS_REGION to the region the app runs in."
export AWS_REGION
aws sts get-caller-identity >/dev/null 2>&1 \
    || die "AWS credentials are missing or expired. Check AWS_PROFILE, or sign in again."

# The cluster and the service are both named after the app.
TASK_ARN="$(aws ecs list-tasks --cluster "$APP_NAME" --service-name "$APP_NAME" \
    --desired-status RUNNING --query 'taskArns[0]' --output text 2>/dev/null)" \
    || die "No app '$APP_NAME' in $AWS_REGION. Is the app stack up? Check AUDIT_APP_NAME and AWS_REGION."
[ -n "$TASK_ARN" ] && [ "$TASK_ARN" != "None" ] \
    || die "The app has no running task yet. If it was just created, wait a minute and try again."

STATE="$(aws ecs describe-tasks --cluster "$APP_NAME" --tasks "$TASK_ARN" --query \
    "tasks[0].containers[?name=='frontend'] | [0].[runtimeId, managedAgents[?name=='ExecuteCommandAgent'] | [0].lastStatus]" \
    --output text)"
RUNTIME_ID="$(echo "$STATE" | awk '{print $1}')"
AGENT="$(echo "$STATE" | awk '{print $2}')"
[ "$AGENT" = "RUNNING" ] \
    || die "The task is starting (ECS Exec agent: ${AGENT:-not reported}). Wait a minute and try again."

echo "Open http://127.0.0.1:$PORT  -  Ctrl-C closes the tunnel."
exec aws ssm start-session \
    --target "ecs:${APP_NAME}_${TASK_ARN##*/}_${RUNTIME_ID}" \
    --document-name AWS-StartPortForwardingSession \
    --parameters "{\"portNumber\":[\"80\"],\"localPortNumber\":[\"$PORT\"]}"
