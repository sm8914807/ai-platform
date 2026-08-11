# Terraform Provider for AI Platform

Phase 3 provides Terraform integration via:

1. **Export API** — `POST /v1/{namespace}/terraform/export`
2. **CLI** — `platform tf-export --output ./terraform`
3. **Generated HCL** — per-resource `.tf` files + `provider.tf`

## Quick start

```bash
# Export from running control plane
platform tf-export --namespace default-org/default-project --output ./deploy/terraform/generated

# Or via API
curl -X POST "http://localhost:8080/v1/default-org/default-project/terraform/export?directory=./terraform"
```

## Provider configuration

Generated `provider.tf`:

```hcl
terraform {
  required_providers {
    platform = {
      source  = "platform.ai/platform"
      version = ">= 0.1.0"
    }
  }
}

provider "platform" {
  endpoint   = var.platform_endpoint
  api_key    = var.platform_api_key
  namespace  = "default-org/default-project"
}
```

## Resource example

```hcl
resource "platform_prompt" "support_v3" {
  namespace = "default-org/default-project"
  version   = "1.0.0"
  spec      = jsonencode({
    template = "You are a support agent. User: {{ input }}"
  })
}
```

## Native Go provider (roadmap)

A full `terraform-provider-platform` Go plugin can wrap the same REST API.
For Phase 3, use export + `platform apply` for bi-directional sync:

```bash
platform apply -f ./resources/
```
