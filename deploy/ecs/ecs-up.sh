#!/usr/bin/env bash
# From a clone: build both images, push them to your own ECR, create or update both stacks.
# Settings: AWS_PROFILE, AWS_REGION, AUDIT_APP_NAME. See deploy/ecs/README.md.

# shellcheck source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

check_aws
need docker "Install Docker: https://docs.docker.com/get-docker/"
need git "Install git."
need python3 "Install Python 3.9 or newer."
docker info >/dev/null 2>&1 || die "Docker is installed but not running. Start it and try again."
COMPOSE_CMD="$(compose_command)"
read -r -a COMPOSE <<<"$COMPOSE_CMD"

# The images get the Docker engine's CPU type; the task must match it.
case "$(docker info --format '{{.Architecture}}')" in
    aarch64 | arm64) ARCH=ARM64 ;;
    x86_64 | amd64) ARCH=X86_64 ;;
    *) die "Unsupported Docker architecture: $(docker info --format '{{.Architecture}}')" ;;
esac

# Tags are immutable in ECR, so a build of uncommitted changes gets a unique one.
TAG="$(git -C "$REPO_ROOT" rev-parse --short HEAD)"
if [ -n "$(git -C "$REPO_ROOT" status --porcelain)" ]; then
    TAG="${TAG}-dirty-$(date +%Y%m%d%H%M%S)"
fi

say "Base stack $BASE_STACK (data and image repositories)"
aws cloudformation deploy \
    --stack-name "$BASE_STACK" \
    --template-file "$SCRIPT_DIR/base.yaml" \
    --parameter-overrides AppName="$APP_NAME" CreateImageRepositories=true \
    --tags Project="$APP_NAME" \
    --no-fail-on-empty-changeset
aws cloudformation update-termination-protection --enable-termination-protection \
    --stack-name "$BASE_STACK" >/dev/null

BACKEND_REPO="$(stack_output "$BASE_STACK" BackendRepositoryUri)"
FRONTEND_REPO="$(stack_output "$BASE_STACK" FrontendRepositoryUri)"
BACKEND_IMAGE="$BACKEND_REPO:$TAG"
FRONTEND_IMAGE="$FRONTEND_REPO:$TAG"

image_pushed() {
    aws ecr describe-images --repository-name "${1##*/}" --image-ids imageTag="$TAG" >/dev/null 2>&1
}

if image_pushed "$BACKEND_REPO" && image_pushed "$FRONTEND_REPO"; then
    say "Images for $TAG are already in ECR"
else
    say "Building images $TAG ($ARCH, no agent); the backend build runs the test suite"
    aws ecr get-login-password | docker login --username AWS --password-stdin "${BACKEND_REPO%%/*}"
    # /dev/null: the compose file names a credentials secret a build does not need.
    (
        cd "$REPO_ROOT"
        export AUDIT_AGENT_PROVIDER=none AUDIT_AWS_CREDENTIALS_FILE=/dev/null
        export AUDIT_BACKEND_IMAGE="$BACKEND_IMAGE" AUDIT_FRONTEND_IMAGE="$FRONTEND_IMAGE"
        "${COMPOSE[@]}" build
        "${COMPOSE[@]}" push
    )
fi

say "App stack $APP_STACK"
TEMPLATE="$REPO_ROOT/build/ecs/aws-cleanup-app.yaml"
python3 "$REPO_ROOT/tools/build_ecs_template.py" --output "$TEMPLATE" >/dev/null
aws cloudformation deploy \
    --stack-name "$APP_STACK" \
    --template-file "$TEMPLATE" \
    --capabilities CAPABILITY_IAM \
    --parameter-overrides AppName="$APP_NAME" BackendImage="$BACKEND_IMAGE" \
        FrontendImage="$FRONTEND_IMAGE" CpuArchitecture="$ARCH" \
    --tags Project="$APP_NAME" \
    --no-fail-on-empty-changeset

say "Waiting for the app to be running"
aws ecs wait services-stable --cluster "$APP_NAME" --services "$APP_NAME"

say "Up. Connect with: $SCRIPT_DIR/ecs-connect.sh   (take it down with ecs-down.sh)"
