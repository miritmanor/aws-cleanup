#!/usr/bin/env bash
# Take the app down: delete the app stack. The base stack, and the data in it, stay.
# Settings: AWS_PROFILE, AWS_REGION, AUDIT_APP_NAME. See deploy/ecs/README.md.

# shellcheck source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

check_aws
if ! stack_exists "$APP_STACK"; then
    say "$APP_STACK does not exist in $AWS_REGION; nothing to take down"
    exit 0
fi
echo "A scan in progress is lost. The data in $BASE_STACK is kept."
delete_stack "$APP_STACK"
say "Down. Bring it back with ecs-up.sh, or by creating $APP_STACK again."
