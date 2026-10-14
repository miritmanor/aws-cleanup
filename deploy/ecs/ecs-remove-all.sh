#!/usr/bin/env bash
# Remove everything: the app stack, then the base stack WITH THE DATA. Asks first.
# Settings: AWS_PROFILE, AWS_REGION, AUDIT_APP_NAME. See deploy/ecs/README.md.

# shellcheck source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

check_aws
if ! stack_exists "$BASE_STACK"; then
    bash "$SCRIPT_DIR/ecs-down.sh"
    say "$BASE_STACK does not exist in $AWS_REGION; nothing else to remove"
    exit 0
fi

FS_ID="$(stack_output "$BASE_STACK" FileSystemId)"
BYTES="$(aws efs describe-file-systems --file-system-id "$FS_ID" \
    --query 'FileSystems[0].SizeInBytes.Value' --output text)"
SIZE="$(awk -v b="$BYTES" 'BEGIN { printf "%.1f MB", b / 1048576 }')"

cat <<EOF

This deletes, in account $ACCOUNT, region $AWS_REGION:
  - stack $APP_STACK, if it exists: the running app, its network and its logs
  - stack $BASE_STACK: file system $FS_ID ($SIZE), which holds the last scan,
    saved project names, merges and the web app's settings
  - the image repositories in $BASE_STACK, if any, with every image in them

There is no undo.

EOF
read -r -p "Type the app name ($APP_NAME) to confirm: " ANSWER
[ "$ANSWER" = "$APP_NAME" ] || die "Not confirmed; nothing was deleted."

if stack_exists "$APP_STACK"; then
    delete_stack "$APP_STACK"
fi
aws cloudformation update-termination-protection --no-enable-termination-protection \
    --stack-name "$BASE_STACK" >/dev/null
delete_stack "$BASE_STACK"
say "Removed. Nothing tagged Project=$APP_NAME should remain from these stacks."
