#!/usr/bin/env bash
# =============================================================================
# Ad Seller System — AgentCore CLI Deploy Script
# =============================================================================
# Deploys the seller agent to Amazon Bedrock AgentCore using the agentcore CLI.
# Supports multiple deployment modes and storage backends.
# Must run from repo root. CLI creates .bedrock_agentcore.yaml in repo root.
#
# Usage:
#   bash infra/aws/agentcore/deploy.sh --mode all --profile genai
#   bash infra/aws/agentcore/deploy.sh --mode mcp --profile genai
#   bash infra/aws/agentcore/deploy.sh --mode http --storage postgres --profile genai
#   bash infra/aws/agentcore/deploy.sh --mode chat --profile genai --test
#   bash infra/aws/agentcore/deploy.sh --profile genai --test-only
#
# Options:
#   --mode MODE         Deployment mode: all|mcp|http|crew|chat (default: chat)
#   --storage STORAGE   Storage backend: sqlite|postgres (default: sqlite)
#   --region REGION     AWS region (default: us-west-2)
#   --name NAME         AgentCore runtime name override
#   --profile PROFILE   AWS CLI profile
#   --test              Deploy then invoke + check CloudWatch logs
#   --test-only         Skip deploy, just invoke + check logs
#   --prompt JSON       Custom invoke payload (default: {"prompt": "list products"})
# =============================================================================

set -euo pipefail

# ── Defaults ────────────────────────────────────────────────────────
REGION="${AWS_REGION:-us-west-2}"
AGENT_NAME="${AGENT_NAME:-}"
AWS_PROFILE="${AWS_PROFILE:-}"
LLM_MODEL="${DEFAULT_LLM_MODEL:-bedrock/us.amazon.nova-pro-v1:0}"
DO_TEST=false
TEST_ONLY=false
DO_CLEANUP=false
PROMPT='{"prompt": "list products"}'
DEPLOY_MODE="chat"
STORAGE_TYPE="sqlite"
INVENTORY_TYPE="${AD_SERVER_TYPE:-csv}"
ENVIRONMENT="${ENVIRONMENT:-staging}"
STACK_PREFIX="${STACK_PREFIX:-ad-seller-${ENVIRONMENT}}"
TEMPLATE_BUCKET="${TEMPLATE_BUCKET:-}"
TEMPLATE_PREFIX="${TEMPLATE_PREFIX:-cloudformation}"

# ── Valid modes ─────────────────────────────────────────────────────
VALID_MODES="all mcp http crew chat"

# ── Parse arguments ─────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)       DEPLOY_MODE="$2"; shift 2 ;;
    --storage)    STORAGE_TYPE="$2"; shift 2 ;;
    --inventory)  INVENTORY_TYPE="$2"; shift 2 ;;
    --region)     REGION="$2"; shift 2 ;;
    --name)       AGENT_NAME="$2"; shift 2 ;;
    --profile)    AWS_PROFILE="$2"; shift 2 ;;
    --test)       DO_TEST=true; shift ;;
    --test-only)  TEST_ONLY=true; DO_TEST=true; shift ;;
    --cleanup)    DO_CLEANUP=true; shift ;;
    --prompt)     PROMPT="$2"; shift 2 ;;
    -h|--help)
      cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  --mode MODE           Deployment mode: all|mcp|http|crew|chat (default: chat)
  --inventory SOURCE    Inventory data source: csv|s3|gam|freewheel (default: csv)
  --storage BACKEND     Deal/order persistence: sqlite|postgres (default: sqlite)
  --region REGION       AWS region (default: us-west-2)
  --name NAME           AgentCore runtime name override
  --profile PROFILE     AWS CLI profile
  --test                Deploy then invoke + check CloudWatch logs
  --test-only           Skip deploy, just invoke + check logs
  --cleanup             Destroy deployed runtimes (and CFN stack if --storage postgres)
  --prompt JSON         Custom invoke payload

Modes:
  all    Deploy both MCP and HTTP runtimes
  mcp    Deploy MCP protocol runtime only (staging_aamp_seller_mcp)
  http   Deploy HTTP protocol runtime only (staging_aamp_seller_http)
  crew   Deploy HTTP runtime with ROUTING_MODE=crew default
  chat   Deploy HTTP runtime with ROUTING_MODE=chat default

Inventory (--inventory):
  csv        Local CSV files in data/csv/samples/ (default, no infra needed)
  s3         S3 bucket — reads CSVs at runtime, no redeploy for data updates
  gam        Google Ad Manager API
  freewheel  FreeWheel API

Storage (--storage):
  sqlite     In-memory SQLite, PUBLIC network mode (default)
  postgres   Deploy CloudFormation infra (Aurora + Redis), VPC network mode

Examples:
  $(basename "$0") --mode http --profile genai                     # CSV + SQLite (simplest)
  $(basename "$0") --mode http --inventory s3 --profile genai      # S3 data, no redeploy for updates
  $(basename "$0") --mode http --inventory gam --storage postgres   # Production (GAM + Aurora)
  --cleanup --mode all               Destroy both MCP and HTTP runtimes
  --cleanup --mode all --storage postgres  Also delete CloudFormation stack
EOF
      exit 0 ;;
    *) echo "ERROR: Unknown option: $1" >&2; exit 1 ;;
  esac
done

# ── Validate mode ───────────────────────────────────────────────────
if ! echo "${VALID_MODES}" | grep -qw "${DEPLOY_MODE}"; then
  echo "ERROR: Invalid mode '${DEPLOY_MODE}'. Must be one of: ${VALID_MODES}" >&2
  exit 1
fi

# ── Validate inventory ──────────────────────────────────────────────
if [[ "${INVENTORY_TYPE}" != "csv" && "${INVENTORY_TYPE}" != "s3" && "${INVENTORY_TYPE}" != "gam" && "${INVENTORY_TYPE}" != "freewheel" ]]; then
  echo "ERROR: Invalid inventory '${INVENTORY_TYPE}'. Must be: csv|s3|gam|freewheel" >&2
  exit 1
fi

# ── Validate storage ────────────────────────────────────────────────
if [[ "${STORAGE_TYPE}" != "sqlite" && "${STORAGE_TYPE}" != "postgres" ]]; then
  echo "ERROR: Invalid storage '${STORAGE_TYPE}'. Must be: sqlite|postgres" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
CFN_DIR="${REPO_ROOT}/infra/aws/cloudformation"

# ── Resolve agent names ─────────────────────────────────────────────
if [[ -n "${AGENT_NAME}" && "${DEPLOY_MODE}" == "all" ]]; then
  # --mode all with --name: append _mcp/_http suffixes to base name
  MCP_AGENT_NAME="${AGENT_NAME}_mcp"
  HTTP_AGENT_NAME="${AGENT_NAME}_http"
elif [[ -n "${AGENT_NAME}" ]]; then
  # Single mode with --name: use name directly for both (only one deploys)
  MCP_AGENT_NAME="${AGENT_NAME}"
  HTTP_AGENT_NAME="${AGENT_NAME}"
else
  # No --name: use defaults
  MCP_AGENT_NAME="staging_aamp_seller_mcp"
  HTTP_AGENT_NAME="staging_aamp_seller_http"
fi

if [[ -n "${AWS_PROFILE}" ]]; then
  export AWS_PROFILE
fi

# Must run from repo root
cd "${REPO_ROOT}"

# =============================================================================
# Infrastructure deployment (postgres mode only)
# =============================================================================
deploy_infrastructure() {
  echo "============================================="
  echo "  Deploying CloudFormation Infrastructure"
  echo "============================================="

  local stack_name="${STACK_PREFIX}-agentcore"
  local db_password_param="${DB_PASSWORD_SSM_PARAM:-/ad-seller/db-password}"
  local account_id
  account_id=$(aws sts get-caller-identity --query Account --output text --region "${REGION}")

  # ── Auto-create S3 bucket for nested templates ──────────────────
  if [[ -z "${TEMPLATE_BUCKET}" ]]; then
    TEMPLATE_BUCKET="ad-seller-cfn-${account_id}-${REGION}"
    echo "  Auto-creating template bucket: ${TEMPLATE_BUCKET}"
  fi

  if ! aws s3 ls "s3://${TEMPLATE_BUCKET}" --region "${REGION}" 2>/dev/null; then
    echo ">>> Creating S3 bucket: ${TEMPLATE_BUCKET}"
    aws s3api create-bucket \
      --bucket "${TEMPLATE_BUCKET}" \
      --region "${REGION}" \
      --create-bucket-configuration LocationConstraint="${REGION}" 2>/dev/null \
      || aws s3 mb "s3://${TEMPLATE_BUCKET}" --region "${REGION}"
  fi

  # ── Auto-create DB password SSM parameter ───────────────────────
  if ! aws ssm get-parameter --name "${db_password_param}" --region "${REGION}" 2>/dev/null; then
    local generated_password
    generated_password=$(python3 -c "import secrets, string; print(secrets.token_urlsafe(24))")
    echo ">>> Creating SSM parameter: ${db_password_param}"
    aws ssm put-parameter \
      --name "${db_password_param}" \
      --type String \
      --value "${generated_password}" \
      --description "Auto-generated Aurora master password for ad-seller" \
      --region "${REGION}"
    echo "  ⚠️  Password stored in SSM — retrieve with:"
    echo "     aws ssm get-parameter --name ${db_password_param} --with-decryption --region ${REGION}"
  else
    echo "  SSM parameter ${db_password_param} already exists"
  fi

  # ── Package and deploy ─────────────────────────────────────────
  # aws cloudformation package uploads nested templates to S3 automatically
  echo ">>> Packaging templates (uploading nested stacks to S3)..."
  local packaged_template="${REPO_ROOT}/.packaged-agentcore.yaml"
  aws cloudformation package \
    --template-file "${SCRIPT_DIR}/main-agentcore.yaml" \
    --s3-bucket "${TEMPLATE_BUCKET}" \
    --s3-prefix "${TEMPLATE_PREFIX}" \
    --output-template-file "${packaged_template}" \
    --region "${REGION}"

  # Look up the private route table from the VPC (needed for S3 gateway endpoint).
  # network.yaml doesn't export it, so we discover it from the subnet associations.
  local private_route_table=""
  if [[ -n "${stack_name}" ]]; then
    # After first deploy, read subnet from stack outputs; before that, discover from VPC
    local vpc_id
    vpc_id=$(aws cloudformation describe-stacks --stack-name "${stack_name}" --region "${REGION}" \
      --query "Stacks[0].Outputs[?OutputKey=='VpcId'].OutputValue" --output text 2>/dev/null || echo "")
    if [[ -n "${vpc_id}" ]]; then
      private_route_table=$(aws ec2 describe-route-tables \
        --filters "Name=vpc-id,Values=${vpc_id}" "Name=association.main,Values=false" \
        --region "${REGION}" \
        --query "RouteTables[?Associations[?SubnetId!=null && !Main]].RouteTableId | [0]" \
        --output text 2>/dev/null || echo "")
      if [[ "${private_route_table}" == "None" ]]; then
        private_route_table=""
      fi
    fi
  fi
  if [[ -n "${private_route_table}" ]]; then
    echo "  Private route table: ${private_route_table}"
  else
    echo "  ⚠️  No private route table found — S3 gateway endpoint will be skipped"
  fi

  # Deploy the root stack
  echo ">>> Deploying stack: ${stack_name}"
  local param_overrides=(
    "Environment=${ENVIRONMENT}"
    "DBMasterPasswordSSMParam=${db_password_param}"
    "VpcCidr=10.20.0.0/16"
  )
  if [[ -n "${private_route_table}" ]]; then
    param_overrides+=("PrivateRouteTableId=${private_route_table}")
  fi

  aws cloudformation deploy \
    --template-file "${packaged_template}" \
    --stack-name "${stack_name}" \
    --parameter-overrides "${param_overrides[@]}" \
    --capabilities CAPABILITY_IAM \
    --region "${REGION}" \
    --no-fail-on-empty-changeset

  # Read stack outputs
  echo ">>> Reading stack outputs..."
  STACK_OUTPUTS=$(aws cloudformation describe-stacks \
    --stack-name "${stack_name}" \
    --region "${REGION}" \
    --query "Stacks[0].Outputs" \
    --output json)

  AURORA_ENDPOINT=$(echo "${STACK_OUTPUTS}" | python3 -c "
import json, sys
outputs = json.load(sys.stdin)
for o in outputs:
    if o['OutputKey'] == 'AuroraEndpoint':
        print(o['OutputValue'])
        break
" 2>/dev/null || echo "")

  AURORA_PORT=$(echo "${STACK_OUTPUTS}" | python3 -c "
import json, sys
outputs = json.load(sys.stdin)
for o in outputs:
    if o['OutputKey'] == 'AuroraPort':
        print(o['OutputValue'])
        break
" 2>/dev/null || echo "5432")

  REDIS_ENDPOINT=$(echo "${STACK_OUTPUTS}" | python3 -c "
import json, sys
outputs = json.load(sys.stdin)
for o in outputs:
    if o['OutputKey'] == 'RedisEndpoint':
        print(o['OutputValue'])
        break
" 2>/dev/null || echo "")

  REDIS_PORT=$(echo "${STACK_OUTPUTS}" | python3 -c "
import json, sys
outputs = json.load(sys.stdin)
for o in outputs:
    if o['OutputKey'] == 'RedisPort':
        print(o['OutputValue'])
        break
" 2>/dev/null || echo "6379")

  VPC_SECURITY_GROUP=$(echo "${STACK_OUTPUTS}" | python3 -c "
import json, sys
outputs = json.load(sys.stdin)
for o in outputs:
    if o['OutputKey'] == 'AgentCoreSecurityGroupId':
        print(o['OutputValue'])
        break
" 2>/dev/null || echo "")

  VPC_SUBNET_1=$(echo "${STACK_OUTPUTS}" | python3 -c "
import json, sys
outputs = json.load(sys.stdin)
for o in outputs:
    if o['OutputKey'] == 'PrivateSubnet1Id':
        print(o['OutputValue'])
        break
" 2>/dev/null || echo "")

  VPC_SUBNET_2=$(echo "${STACK_OUTPUTS}" | python3 -c "
import json, sys
outputs = json.load(sys.stdin)
for o in outputs:
    if o['OutputKey'] == 'PrivateSubnet2Id':
        print(o['OutputValue'])
        break
" 2>/dev/null || echo "")

  echo "  Aurora   : ${AURORA_ENDPOINT}:${AURORA_PORT}"
  echo "  Redis    : ${REDIS_ENDPOINT}:${REDIS_PORT}"
  echo "  SG       : ${VPC_SECURITY_GROUP}"
  echo "  Subnets  : ${VPC_SUBNET_1}, ${VPC_SUBNET_2}"

  # Build connection URLs with actual password
  DB_PASSWORD=$(aws ssm get-parameter --name "${db_password_param}" --region "${REGION}" --query 'Parameter.Value' --output text)
  DB_URL="postgresql+asyncpg://seller:${DB_PASSWORD}@${AURORA_ENDPOINT}:${AURORA_PORT}/ad_seller"
  REDIS_URL="redis://${REDIS_ENDPOINT}:${REDIS_PORT}/0"

  echo "✅ Infrastructure deployed"
}

# =============================================================================
# S3 Data Bucket provisioning (--inventory s3)
# =============================================================================
provision_s3_data_bucket() {
  local bucket_name="${S3_DATA_BUCKET:-${STACK_PREFIX}-seller-data-${REGION}}"
  local prefix="${S3_DATA_PREFIX:-seller-data/}"
  local stack_name="${STACK_PREFIX}-s3-data"

  echo "============================================="
  echo "  Provisioning S3 Data Bucket"
  echo "  Bucket: ${bucket_name}"
  echo "  Prefix: ${prefix}"
  echo "============================================="

  # Get the runtime execution role ARN (needed for IAM policy in the stack)
  local runtime_role_arn=""
  if [[ -f "${REPO_ROOT}/.bedrock_agentcore.yaml" ]]; then
    runtime_role_arn=$(grep "execution_role:" "${REPO_ROOT}/.bedrock_agentcore.yaml" | head -1 | awk '{print $2}')
  fi

  # Deploy CloudFormation stack
  local template_path="${SCRIPT_DIR}/storage-s3.yaml"
  if [[ -f "${template_path}" ]]; then
    echo "  Deploying CloudFormation stack: ${stack_name}..."

    local param_overrides="Environment=${ENVIRONMENT} AgentName=${STACK_PREFIX}"
    if [[ -n "${runtime_role_arn}" ]]; then
      param_overrides="${param_overrides} RuntimeRoleArn=${runtime_role_arn}"
    fi
    if [[ -n "${S3_DATA_BUCKET}" ]]; then
      param_overrides="${param_overrides} BucketName=${S3_DATA_BUCKET}"
    fi

    local cfn_cmd="aws cloudformation deploy"
    cfn_cmd="${cfn_cmd} --template-file ${template_path}"
    cfn_cmd="${cfn_cmd} --stack-name ${stack_name}"
    cfn_cmd="${cfn_cmd} --region ${REGION}"
    cfn_cmd="${cfn_cmd} --parameter-overrides ${param_overrides}"
    cfn_cmd="${cfn_cmd} --capabilities CAPABILITY_IAM"
    cfn_cmd="${cfn_cmd} --no-fail-on-empty-changeset"
    if [[ -n "${AWS_PROFILE}" ]]; then
      cfn_cmd="${cfn_cmd} --profile ${AWS_PROFILE}"
    fi

    eval ${cfn_cmd}

    # Get the bucket name from stack outputs
    bucket_name=$(aws cloudformation describe-stacks \
      --stack-name "${stack_name}" --region "${REGION}" \
      ${AWS_PROFILE:+--profile "${AWS_PROFILE}"} \
      --query "Stacks[0].Outputs[?OutputKey=='DataBucketName'].OutputValue" \
      --output text 2>/dev/null || echo "${bucket_name}")

    echo "  ✅ Stack deployed: ${stack_name} → bucket: ${bucket_name}"
  else
    # Fallback: create bucket imperatively if template not found
    echo "  ⚠️  CloudFormation template not found, creating bucket imperatively..."
    if aws s3api head-bucket --bucket "${bucket_name}" --region "${REGION}" \
       ${AWS_PROFILE:+--profile "${AWS_PROFILE}"} 2>/dev/null; then
      echo "  ✅ Bucket already exists: ${bucket_name}"
    else
      if [[ "${REGION}" == "us-east-1" ]]; then
        aws s3api create-bucket --bucket "${bucket_name}" --region "${REGION}" \
          ${AWS_PROFILE:+--profile "${AWS_PROFILE}"}
      else
        aws s3api create-bucket --bucket "${bucket_name}" --region "${REGION}" \
          ${AWS_PROFILE:+--profile "${AWS_PROFILE}"} \
          --create-bucket-configuration LocationConstraint="${REGION}"
      fi
      aws s3api put-public-access-block --bucket "${bucket_name}" \
        ${AWS_PROFILE:+--profile "${AWS_PROFILE}"} \
        --public-access-block-configuration \
        "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
      echo "  ✅ Bucket created: ${bucket_name}"
    fi
  fi

  # Upload local CSV data files to S3
  local data_dir="${REPO_ROOT}/data/csv/samples/aws_workshop"
  if [[ -d "${data_dir}" ]]; then
    echo "  Uploading CSV data to s3://${bucket_name}/${prefix}..."
    local count=0
    for csv_file in "${data_dir}"/inventory*.csv "${data_dir}"/audiences*.csv "${data_dir}"/orders*.csv "${data_dir}"/deals*.csv "${data_dir}"/line_items*.csv; do
      if [[ -f "${csv_file}" ]]; then
        local fname=$(basename "${csv_file}")
        aws s3 cp "${csv_file}" "s3://${bucket_name}/${prefix}${fname}" \
          --region "${REGION}" ${AWS_PROFILE:+--profile "${AWS_PROFILE}"} --quiet
        count=$((count + 1))
      fi
    done
    echo "  ✅ Uploaded ${count} CSV file(s) to s3://${bucket_name}/${prefix}"
  else
    echo "  ⚠️  No local data dir found: ${data_dir}"
  fi

  # Export for use in env vars
  export S3_DATA_BUCKET="${bucket_name}"
  export S3_DATA_PREFIX="${prefix}"
}

# =============================================================================
# MCP Runtime deployment
# =============================================================================
deploy_mcp_runtime() {
  local agent_name="${1:-${MCP_AGENT_NAME}}"
  echo ""
  echo "============================================="
  echo "  Deploying MCP Runtime: ${agent_name}"
  echo "============================================="

  # Ensure CLI is installed
  if ! command -v agentcore &>/dev/null; then
    echo ">>> Installing agentcore CLI..."
    pip install bedrock-agentcore-starter-toolkit==0.3.4
  fi

  # Configure for MCP protocol
  echo ">>> Configuring MCP runtime..."

  # Build configure args — add VPC networking for postgres storage
  local configure_args=(
    -e src/ad_seller/interfaces/agentcore/mcp_main.py
    -n "${agent_name}"
    -rf infra/aws/agentcore/requirements.txt
    -p MCP
    -r "${REGION}"
    --non-interactive
    --deployment-type container
  )
  if [[ "${STORAGE_TYPE}" == "postgres" && -n "${VPC_SECURITY_GROUP}" ]]; then
    configure_args+=(
      --vpc
      --subnets "${VPC_SUBNET_1},${VPC_SUBNET_2}"
      --security-groups "${VPC_SECURITY_GROUP}"
    )
    echo "  VPC mode: SG=${VPC_SECURITY_GROUP}, Subnets=${VPC_SUBNET_1},${VPC_SUBNET_2}"
  fi

  agentcore configure "${configure_args[@]}"

  # Build env var args — AGENTCORE_MODE tells main.py to run MCP server
  local env_args=(
    --env "AGENTCORE_MODE=mcp"
    --env "DEFAULT_LLM_MODEL=${LLM_MODEL}"
    --env "MANAGER_LLM_MODEL=${LLM_MODEL}"
    --env "ANTHROPIC_API_KEY=not-used-with-bedrock"
    --env "DATABASE_URL=sqlite:///:memory:"
    --env "CREW_MEMORY_ENABLED=true"
    --env "MEMORY_LLM_MODEL=bedrock/us.amazon.nova-lite-v1:0"
  )

  if [[ "${INVENTORY_TYPE}" == "s3" ]]; then
    env_args+=(
      --env "AD_SERVER_TYPE=s3"
      --env "S3_DATA_BUCKET=${S3_DATA_BUCKET:-${STACK_PREFIX}-seller-data-${REGION}}"
      --env "S3_DATA_PREFIX=${S3_DATA_PREFIX:-seller-data/}"
      --env "STORAGE_TYPE=sqlite"
    )
  elif [[ "${STORAGE_TYPE}" == "postgres" ]]; then
    env_args+=(
      --env "AD_SERVER_TYPE=${INVENTORY_TYPE}"
      --env "CSV_DATA_DIR=./data/csv/samples/aws_workshop"
      --env "STORAGE_TYPE=hybrid"
      --env "DATABASE_URL=${DB_URL}"
      --env "REDIS_URL=${REDIS_URL}"
    )
  else
    env_args+=(
      --env "AD_SERVER_TYPE=${INVENTORY_TYPE}"
      --env "CSV_DATA_DIR=./data/csv/samples/aws_workshop"
      --env "STORAGE_TYPE=sqlite"
    )
  fi

  # Deploy
  echo ">>> Deploying MCP runtime..."
  agentcore deploy "${env_args[@]}" --auto-update-on-conflict

  echo "✅ MCP runtime deployed: ${agent_name}"
}

# =============================================================================
# HTTP Runtime deployment
# =============================================================================
deploy_http_runtime() {
  local agent_name="${1:-${HTTP_AGENT_NAME}}"
  local routing_mode="${2:-chat}"
  echo ""
  echo "============================================="
  echo "  Deploying HTTP Runtime: ${agent_name}"
  echo "  Routing Mode: ${routing_mode}"
  echo "============================================="

  # Ensure CLI is installed
  if ! command -v agentcore &>/dev/null; then
    echo ">>> Installing agentcore CLI..."
    pip install bedrock-agentcore-starter-toolkit==0.3.4
  fi

  # Configure for HTTP protocol
  echo ">>> Configuring HTTP runtime..."

  # Build configure args — add VPC networking for postgres storage
  local configure_args=(
    -e src/ad_seller/interfaces/agentcore/http_main.py
    -n "${agent_name}"
    -rf infra/aws/agentcore/requirements.txt
    -p HTTP
    -r "${REGION}"
    --non-interactive
    --deployment-type container
  )
  if [[ "${STORAGE_TYPE}" == "postgres" && -n "${VPC_SECURITY_GROUP}" ]]; then
    configure_args+=(
      --vpc
      --subnets "${VPC_SUBNET_1},${VPC_SUBNET_2}"
      --security-groups "${VPC_SECURITY_GROUP}"
    )
    echo "  VPC mode: SG=${VPC_SECURITY_GROUP}, Subnets=${VPC_SUBNET_1},${VPC_SUBNET_2}"
  fi

  agentcore configure "${configure_args[@]}"

  # Build env var args — AGENTCORE_MODE tells main.py to run HTTP server
  local env_args=(
    --env "AGENTCORE_MODE=http"
    --env "DEFAULT_LLM_MODEL=${LLM_MODEL}"
    --env "MANAGER_LLM_MODEL=${LLM_MODEL}"
    --env "ROUTING_MODE=${routing_mode}"
    --env "ANTHROPIC_API_KEY=not-used-with-bedrock"
    --env "DATABASE_URL=sqlite:///:memory:"
    --env "CREW_MEMORY_ENABLED=true"
    --env "MEMORY_LLM_MODEL=bedrock/us.amazon.nova-lite-v1:0"
  )

  if [[ "${INVENTORY_TYPE}" == "s3" ]]; then
    env_args+=(
      --env "AD_SERVER_TYPE=s3"
      --env "S3_DATA_BUCKET=${S3_DATA_BUCKET:-${STACK_PREFIX}-seller-data-${REGION}}"
      --env "S3_DATA_PREFIX=${S3_DATA_PREFIX:-seller-data/}"
      --env "STORAGE_TYPE=sqlite"
    )
  elif [[ "${STORAGE_TYPE}" == "postgres" ]]; then
    env_args+=(
      --env "AD_SERVER_TYPE=${INVENTORY_TYPE}"
      --env "CSV_DATA_DIR=./data/csv/samples/aws_workshop"
      --env "STORAGE_TYPE=hybrid"
      --env "DATABASE_URL=${DB_URL}"
      --env "REDIS_URL=${REDIS_URL}"
    )
  else
    env_args+=(
      --env "AD_SERVER_TYPE=${INVENTORY_TYPE}"
      --env "CSV_DATA_DIR=./data/csv/samples/aws_workshop"
      --env "STORAGE_TYPE=sqlite"
    )
  fi

  # Deploy
  echo ">>> Deploying HTTP runtime..."
  agentcore deploy "${env_args[@]}" --auto-update-on-conflict

  echo "✅ HTTP runtime deployed: ${agent_name}"
}

# =============================================================================
# Cleanup (--cleanup)
# =============================================================================
if [[ "${DO_CLEANUP}" == "true" ]]; then
  echo "============================================="
  echo "  Cleanup: Destroying AgentCore Resources"
  echo "============================================="
  echo "  Mode    : ${DEPLOY_MODE}"
  echo "  Storage : ${STORAGE_TYPE}"
  echo "  Region  : ${REGION}"
  echo "============================================="

  _destroy_runtime() {
    local agent_name="$1"
    echo ""
    echo ">>> Destroying runtime: ${agent_name}"

    # Set this agent as default in yaml (without reconfiguring)
    if [[ -f .bedrock_agentcore.yaml ]]; then
      sed -i.bak "s/^default_agent:.*/default_agent: ${agent_name}/" .bedrock_agentcore.yaml
      rm -f .bedrock_agentcore.yaml.bak
    fi

    # Destroy runtime, endpoint, ECR images+repo, CodeBuild, IAM role
    agentcore destroy --force --delete-ecr-repo 2>&1 || echo "  ⚠️  destroy failed or agent not found: ${agent_name}"

    # Note: Memory resource persists but is harmless (no cost when idle).
    # Memory records are cleared when sessions terminate.
    echo "  ✅ ${agent_name} destroyed"
  }

  case "${DEPLOY_MODE}" in
    all)
      _destroy_runtime "${MCP_AGENT_NAME}"
      _destroy_runtime "${HTTP_AGENT_NAME}"
      ;;
    mcp)
      _destroy_runtime "${MCP_AGENT_NAME}"
      ;;
    http|crew|chat)
      _destroy_runtime "${HTTP_AGENT_NAME}"
      ;;
  esac

  # Delete CloudFormation stack if --storage postgres
  if [[ "${STORAGE_TYPE}" == "postgres" ]]; then
    local stack_name="${STACK_PREFIX}-agentcore"
    echo ""
    echo ">>> Deleting CloudFormation stack: ${stack_name}"
    aws cloudformation delete-stack \
      --stack-name "${stack_name}" \
      --region "${REGION}" 2>&1 || echo "  ⚠️  Stack delete failed or not found"
    echo "  Waiting for stack deletion..."
    aws cloudformation wait stack-delete-complete \
      --stack-name "${stack_name}" \
      --region "${REGION}" 2>&1 || echo "  ⚠️  Stack wait timed out"
    echo "  ✅ CloudFormation stack deleted"
  fi

  echo ""
  echo "============================================="
  echo "  ✅ Cleanup Complete"
  echo "============================================="
  exit 0
fi

# =============================================================================
# Main dispatch
# =============================================================================
if [[ "${TEST_ONLY}" == "false" ]]; then
  echo "============================================="
  echo "  Ad Seller Agent — AgentCore Deploy"
  echo "============================================="
  echo "  Mode       : ${DEPLOY_MODE}"
  echo "  Inventory  : ${INVENTORY_TYPE}"
  echo "  Storage    : ${STORAGE_TYPE}"
  echo "  Region     : ${REGION}"
  echo "  LLM Model  : ${LLM_MODEL}"
  [[ -n "${AWS_PROFILE}" ]] && echo "  AWS Profile: ${AWS_PROFILE}"
  echo "============================================="

  # Deploy infrastructure if postgres
  if [[ "${STORAGE_TYPE}" == "postgres" ]]; then
    deploy_infrastructure
  fi

  # Provision S3 data bucket if s3 inventory mode
  if [[ "${INVENTORY_TYPE}" == "s3" ]]; then
    provision_s3_data_bucket
  fi

  # Mode dispatch
  case "${DEPLOY_MODE}" in
    all)
      deploy_mcp_runtime
      deploy_http_runtime "${HTTP_AGENT_NAME}" "chat"
      ;;
    mcp)
      deploy_mcp_runtime
      ;;
    http)
      deploy_http_runtime "${HTTP_AGENT_NAME}" "chat"
      ;;
    crew)
      deploy_http_runtime "${HTTP_AGENT_NAME}" "crew"
      ;;
    chat)
      deploy_http_runtime "${HTTP_AGENT_NAME}" "chat"
      ;;
  esac

  echo ""
  echo "✅ Deploy complete (mode=${DEPLOY_MODE}, storage=${STORAGE_TYPE})"
fi

# =============================================================================
# Test (--test or --test-only)
# =============================================================================
if [[ "${DO_TEST}" == "true" ]]; then
  echo ""
  echo "============================================="
  echo "  Testing deployed runtimes"
  echo "============================================="

  # Use pytest-based integration tests if available
  TEST_RUNNER="${REPO_ROOT}/tests/integration/agentcore/run_tests.sh"
  if [[ -f "${TEST_RUNNER}" ]]; then
    echo ">>> Running pytest integration tests..."
    RUNNER_ARGS=(--profile "${AWS_PROFILE:-}")
    if [[ -n "${HTTP_AGENT_NAME}" ]]; then
      RUNNER_ARGS+=(--agent-name "${HTTP_AGENT_NAME}")
    fi
    bash "${TEST_RUNNER}" "${RUNNER_ARGS[@]}"
    exit $?
  fi

  # Fallback: inline tests (if pytest runner not found)
  echo "  ⚠️  pytest runner not found at ${TEST_RUNNER} — using inline tests"

  _test_runtime() {
    local agent_name="$1"
    local test_prompt="$2"
    local label="$3"
    local max_retries=3
    local retry_wait=30

    echo ""
    echo "--- Testing ${label}: ${agent_name} ---"

    # Resolve runtime ARN from yaml
    local runtime_arn=""
    if command -v python3 &>/dev/null && [[ -f .bedrock_agentcore.yaml ]]; then
      runtime_arn=$(python3 -c "
import yaml
with open('.bedrock_agentcore.yaml') as f:
    cfg = yaml.safe_load(f)
agent = cfg['agents'].get('${agent_name}', {})
bc = agent.get('bedrock_agentcore', {})
print(bc.get('agent_arn', ''))
" 2>/dev/null || true)
    fi

    if [[ -z "${runtime_arn}" ]]; then
      echo "  ⚠️  No runtime ARN found for ${agent_name} — skipping"
      return 0
    fi

    local runtime_id
    runtime_id=$(echo "${runtime_arn}" | awk -F'/' '{print $2}')
    local log_group="/aws/bedrock-agentcore/runtimes/${runtime_id}-DEFAULT"
    local date_prefix
    date_prefix=$(date -u +"%Y/%m/%d")

    echo "  ARN:  ${runtime_arn}"

    # Retry loop — VPC cold starts can exceed 120s init timeout on first invoke
    local attempt=1
    local invoke_output=""
    local passed=false

    while [[ ${attempt} -le ${max_retries} ]]; do
      if [[ ${attempt} -gt 1 ]]; then
        echo "  ⏳ Retry ${attempt}/${max_retries} — waiting ${retry_wait}s for cold start..."
        sleep "${retry_wait}"
      fi

      echo "  Invoking (attempt ${attempt}/${max_retries}): ${test_prompt}"
      invoke_output=$(agentcore invoke "${test_prompt}" 2>&1) || true

      # Check for init timeout or transient errors worth retrying
      if echo "${invoke_output}" | grep -qi 'initialization time exceeded\|32010\|RuntimeClientError'; then
        echo "  ⚠️  Cold start timeout (attempt ${attempt}) — runtime still warming up"
        ((attempt++))
        continue
      fi

      # Check for real errors (not retryable)
      if echo "${invoke_output}" | grep -qi '"error":\|"exception":\|Invocation failed'; then
        break  # Real error, don't retry
      fi

      # Success
      passed=true
      break
    done

    # Show response — extract the actual content after the session box
    local response_text
    response_text=$(echo "${invoke_output}" | sed -n '/^Response:/,$ p' | head -60)
    if [[ -z "${response_text}" ]]; then
      response_text=$(echo "${invoke_output}" | grep -v '│\|╭\|╰\|╮\|─' | tail -60)
    fi
    if [[ -n "${response_text}" ]]; then
      echo ""
      echo "  --- Response ---"
      echo "${response_text}"
      echo "  ---"
    fi

    if [[ "${passed}" == "true" ]]; then
      echo "  ✅ ${label} PASSED"
      return 0
    else
      echo "  ❌ ${label} FAILED (after ${attempt} attempts)"
      echo ""
      echo "  CloudWatch logs (last 5 min):"
      aws logs tail "${log_group}" \
        --log-stream-name-prefix "${date_prefix}/[runtime-logs]" \
        --since 5m --format short --region "${REGION}" 2>&1 \
        | grep -v "opentelemetry.instrumentation" \
        | grep -v "otelTrace" \
        | tail -20
      return 1
    fi
  }

  TEST_FAILURES=0

  case "${DEPLOY_MODE}" in
    all)
      # Test HTTP runtime — chat mode
      _test_runtime "${HTTP_AGENT_NAME}" '{"prompt": "list products"}' "Chat mode" || ((TEST_FAILURES++))
      # Test HTTP runtime — crew mode
      _test_runtime "${HTTP_AGENT_NAME}" '{"prompt": "list products", "routing_mode": "crew"}' "Crew mode" || ((TEST_FAILURES++))
      # Note: MCP runtime can't be tested with agentcore invoke (different protocol)
      echo ""
      echo "  ℹ️  MCP runtime (${MCP_AGENT_NAME}) uses MCP protocol — test with MCP client, not agentcore invoke"
      ;;
    mcp)
      echo "  ℹ️  MCP runtime uses MCP protocol — test with MCP client, not agentcore invoke"
      ;;
    http)
      # HTTP runtime supports both routing modes — test all tool paths
      _test_runtime "${HTTP_AGENT_NAME}" '{"prompt": "list products"}' "Chat mode (list)" || ((TEST_FAILURES++))
      _test_runtime "${HTTP_AGENT_NAME}" '{"prompt": "show me all available inventory across CTV, linear, and digital channels with product details and pricing", "routing_mode": "crew"}' "Crew: list_products" || ((TEST_FAILURES++))
      _test_runtime "${HTTP_AGENT_NAME}" '{"prompt": "get pricing for inv-ctv-apex-sports-nba for preferred agency tier with 5M impressions", "routing_mode": "crew"}' "Crew: get_pricing" || ((TEST_FAILURES++))
      _test_runtime "${HTTP_AGENT_NAME}" '{"prompt": "negotiate a deal for inv-ctv-apex-sports-nba at $40 CPM for 3M impressions as a Preferred Deal", "routing_mode": "crew"}' "Crew: create_deal" || ((TEST_FAILURES++))
      _test_runtime "${HTTP_AGENT_NAME}" '{"prompt": "get the rate card organized by inventory type", "routing_mode": "crew"}' "Crew: get_rate_card" || ((TEST_FAILURES++))
      ;;
    chat)
      _test_runtime "${HTTP_AGENT_NAME}" "${PROMPT}" "Chat mode" || ((TEST_FAILURES++))
      ;;
    crew)
      _test_runtime "${HTTP_AGENT_NAME}" '{"prompt": "list products", "routing_mode": "crew"}' "Crew mode" || ((TEST_FAILURES++))
      ;;
  esac

  echo ""
  if [[ ${TEST_FAILURES} -gt 0 ]]; then
    echo "============================================="
    echo "  ❌ ${TEST_FAILURES} TEST(S) FAILED"
    echo "============================================="
    exit 1
  else
    echo "============================================="
    echo "  ✅ ALL TESTS PASSED"
    echo "============================================="
  fi
fi

# =============================================================================
# Status (deploy only, no --test)
# =============================================================================
if [[ "${DO_TEST}" == "false" && "${TEST_ONLY}" == "false" ]]; then
  echo ""
  echo ">>> Deployment status..."
  agentcore status --verbose

  echo ""
  echo "============================================="
  echo "  Deployment Complete"
  echo "============================================="
fi
