# shellcheck shell=bash
# Shared by ecs-up.sh, ecs-down.sh and ecs-remove-all.sh. Sourced, never run directly.
# Settings: AWS_PROFILE, AWS_REGION, AUDIT_APP_NAME (default aws-cleanup).

set -euo pipefail

APP_NAME="${AUDIT_APP_NAME:-aws-cleanup}"
# Unused here; the scripts that source this file read them.
# shellcheck disable=SC2034
BASE_STACK="${APP_NAME}-base"
# shellcheck disable=SC2034
APP_STACK="${APP_NAME}-app"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC2034
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

die() {
    echo "error: $*" >&2
    exit 1
}

say() {
    echo "==> $*"
}

need() {
    command -v "$1" >/dev/null 2>&1 || die "$1 not found. $2"
}

# Sets AWS_REGION and ACCOUNT, or stops with what to fix.
check_aws() {
    need aws "Install AWS CLI v2: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    aws --version 2>&1 | grep -q '^aws-cli/2' || die "AWS CLI v2 is required (found: $(aws --version 2>&1))"
    local region
    region="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || true)}}"
    [ -n "$region" ] || die "No AWS region. Set AWS_REGION, e.g. AWS_REGION=us-east-1"
    export AWS_REGION="$region"
    ACCOUNT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)" \
        || die "AWS credentials are missing or expired. Check AWS_PROFILE, or sign in again (aws login / aws sso login)."
    say "Account $ACCOUNT, region $AWS_REGION, app name $APP_NAME"
}

stack_exists() {
    aws cloudformation describe-stacks --stack-name "$1" >/dev/null 2>&1
}

stack_output() {
    aws cloudformation describe-stacks --stack-name "$1" \
        --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue | [0]" --output text
}

delete_stack() {
    say "Deleting stack $1"
    aws cloudformation delete-stack --stack-name "$1"
    aws cloudformation wait stack-delete-complete --stack-name "$1"
}

# Prints the Compose command: the plugin (docker compose) or the standalone docker-compose.
compose_command() {
    if docker compose version >/dev/null 2>&1; then
        echo "docker compose"
    elif command -v docker-compose >/dev/null 2>&1; then
        echo "docker-compose"
    else
        die "Docker Compose not found: install the compose plugin or docker-compose."
    fi
}
