#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export AWS_PAGER=""
region="${AWS_DEFAULT_REGION:-eu-north-1}"
stack="${1:?Usage: bash deploy.sh STACK [dev|test]}"
environment="${2:-dev}"
[[ "$stack" =~ ^[a-zA-Z][a-zA-Z0-9-]{0,60}$ ]]
[[ "$environment" == dev || "$environment" == test ]]
command -v aws >/dev/null
docker info >/dev/null

awsr() { aws --region "$region" "$@"; }
output() {
  awsr cloudformation describe-stacks --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue | [0]" --output text
}

awsr cloudformation validate-template --template-body file://template.json >/dev/null
awsr cloudformation deploy --stack-name "$stack-ecr" --template-file ecr.yaml --no-fail-on-empty-changeset
repository="$(output "$stack-ecr" RepositoryName)"
uri="$(output "$stack-ecr" RepositoryUri)"
arn="$(awsr ecr describe-repositories --repository-names "$repository" --query 'repositories[0].repositoryArn' --output text)"
tag="$(sha256sum Dockerfile app.py | sha256sum | cut -c1-20)"
if [[ "$(awsr ecr list-images --repository-name "$repository" --query "length(imageIds[?imageTag=='$tag'])" --output text)" == 0 ]]; then
  registry="${uri%%/*}"
  awsr ecr get-login-password | docker login --username AWS --password-stdin "$registry"
  docker build --platform linux/amd64 -t "$uri:$tag" .
  docker push "$uri:$tag"
fi
digest="$(awsr ecr describe-images --repository-name "$repository" --image-ids "imageTag=$tag" --query 'imageDetails[0].imageDigest' --output text)"
if ! awsr cloudformation deploy --stack-name "$stack" --template-file template.json \
    --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset \
    --parameter-overrides "Environment=$environment" "ImageUri=$uri@$digest" "RepositoryArn=$arn"; then
  awsr cloudformation describe-stack-events --stack-name "$stack" \
    --query "StackEvents[?ends_with(ResourceStatus, 'FAILED')].[LogicalResourceId,ResourceStatusReason]" \
    --output table
  exit 1
fi

url="$(output "$stack" ServiceUrl)"
curl -fsS --retry 5 --retry-all-errors --max-time 10 "$url/health"
printf '\nService URL: %s\n' "$url"
awsr elbv2 describe-target-health --target-group-arn "$(output "$stack" TargetGroupArn)" \
  --query 'TargetHealthDescriptions[].{IP:Target.Id,State:TargetHealth.State}' --output table
