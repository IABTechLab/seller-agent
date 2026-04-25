# Deployment

Deploy the seller agent using Docker locally or to AWS with CloudFormation or Terraform.

---

## Quick Start — Docker Compose

The fastest way to run the full stack (app + PostgreSQL + Redis):

```bash
cd infra/docker
docker compose up
```

This starts:

| Service | Port | Purpose |
|---------|------|---------|
| **app** | 8000 | Seller agent API |
| **postgres** | 5432 | Durable business data (products, deals, orders) |
| **redis** | 6379 | Sessions, cache, pubsub |

Verify it's running:

```bash
curl http://localhost:8000/health
```

### Environment Variables

The app container reads from `../../.env` (project root). Key production settings:

```bash
STORAGE_TYPE=hybrid                    # Routes keys to Postgres or Redis
DATABASE_URL=postgresql+asyncpg://seller:seller@postgres:5432/ad_seller
REDIS_URL=redis://redis:6379/0
ANTHROPIC_API_KEY=sk-ant-...           # Or your chosen LLM provider key
```

See [Configuration](configuration.md) for the full variable reference.

### Rebuilding

```bash
docker compose build --no-cache app
docker compose up -d
```

---

## Storage Backends

The seller agent supports three storage modes:

| Mode | `STORAGE_TYPE` | Best For |
|------|---------------|----------|
| **SQLite** | `sqlite` | Local dev, single instance |
| **Redis** | `redis` | Fast ephemeral storage |
| **Hybrid** | `hybrid` | Production — Postgres for business data, Redis for sessions/cache |

### Hybrid Mode (Recommended for Production)

Hybrid mode routes keys by prefix:

- **Redis**: `session:*`, `session_index:*`, `cache:*`, `lock:*`, `pubsub:*`, `rate_limit:*`
- **PostgreSQL**: Everything else (products, deals, orders, proposals, negotiations, quotes, agents, packages)

This gives you durable storage for business data with fast in-memory access for sessions.

```bash
STORAGE_TYPE=hybrid
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/ad_seller
REDIS_URL=redis://host:6379/0
```

### PostgreSQL Connection Pool

For production tuning:

```bash
POSTGRES_POOL_MIN=2    # Minimum connections (default: 2)
POSTGRES_POOL_MAX=10   # Maximum connections (default: 10)
```

---

## AWS Deployment

Two IaC options are provided — choose based on your team's preference:

### CloudFormation

Nested stack templates in `infra/aws/cloudformation/`:

```
cloudformation/
├── main.yaml       # Root stack (orchestrates nested stacks)
├── network.yaml    # VPC, subnets, NAT, security groups
├── storage.yaml    # Aurora Serverless v2, ElastiCache Redis
└── compute.yaml    # ECS Fargate, ALB, CloudWatch, IAM
```

Deploy:

```bash
# Upload nested templates to S3
aws s3 sync infra/aws/cloudformation/ s3://your-bucket/cf-templates/

# Create the stack
aws cloudformation create-stack \
  --stack-name ad-seller-prod \
  --template-url https://your-bucket.s3.amazonaws.com/cf-templates/main.yaml \
  --parameters \
    ParameterKey=Environment,ParameterValue=production \
    ParameterKey=AnthropicApiKey,ParameterValue=sk-ant-... \
    ParameterKey=ContainerImage,ParameterValue=123456789.dkr.ecr.us-east-1.amazonaws.com/ad-seller:latest \
  --capabilities CAPABILITY_NAMED_IAM
```

### Terraform

Modular Terraform in `infra/aws/terraform/`:

```
terraform/
├── main.tf
├── variables.tf
├── outputs.tf
├── terraform.tfvars.example
└── modules/
    ├── network/
    ├── storage/
    └── compute/
```

Deploy:

```bash
cd infra/aws/terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values

terraform init
terraform plan
terraform apply
```

### AWS Architecture

Both options deploy the same architecture:

- **Compute**: ECS Fargate (serverless containers, auto-scaling)
- **Database**: Aurora Serverless v2 PostgreSQL (auto-scaling 0.5–4 ACU)
- **Cache**: ElastiCache Redis (t3.micro)
- **Networking**: VPC with public/private subnets across 2 AZs
- **Load Balancer**: Application Load Balancer with HTTPS
- **Secrets**: SSM Parameter Store (SecureString)
- **Logging**: CloudWatch Logs

---

## Building the Container Image

For ECR deployment:

```bash
# Build
docker build -t ad-seller -f infra/docker/Dockerfile .

# Tag and push to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 123456789.dkr.ecr.us-east-1.amazonaws.com
docker tag ad-seller:latest 123456789.dkr.ecr.us-east-1.amazonaws.com/ad-seller:latest
docker push 123456789.dkr.ecr.us-east-1.amazonaws.com/ad-seller:latest
```

---

## Amazon Bedrock AgentCore

AgentCore provides a managed runtime for the seller agent — no Docker, ECS, or CloudFormation needed. A single CLI command builds and deploys the container.

```bash
bash infra/aws/agentcore/deploy.sh \
  --mode http \
  --name my-seller-agent \
  --profile my-aws-profile \
  --test
```

AgentCore handles container orchestration, IAM roles, ECR, and scaling. The runtime supports two modes:

- **crew**: CrewAI PublisherCrew with Bedrock Converse LLM — full agentic behavior
- **chat**: Existing ChatInterface keyword router — fast deterministic responses

See the [AgentCore Deployment Guide](agentcore-deployment.md) for full details, architecture diagrams, environment variables, and troubleshooting.
