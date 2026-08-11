"""Export platform resources as Terraform JSON / HCL snippets."""

import json
from pathlib import Path
from typing import Any


def resource_to_terraform(
    kind: str,
    name: str,
    namespace: str,
    version: str,
    spec: dict[str, Any],
) -> dict[str, Any]:
    tf_type = f"platform_{kind.lower().replace(' ', '_')}"
    return {
        "resource": {
            tf_type: {
                name.replace("-", "_"): {
                    "namespace": namespace,
                    "version": version,
                    "spec": spec,
                }
            }
        }
    }


def export_terraform_json(published: list[Any], namespace: str) -> str:
    """Generate terraform-json compatible config from published resources."""
    resources: list[dict[str, Any]] = []
    for ver in published:
        if not ver.kind or not ver.name:
            continue
        resources.append(
            resource_to_terraform(
                ver.kind, ver.name, namespace, ver.version, ver.spec_json
            )
        )
    config = {"resources": resources}
    return json.dumps(config, indent=2)


def write_terraform_files(
    published: list[Any],
    namespace: str,
    output_dir: Path,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)

    provider_tf = f'''
terraform {{
  required_providers {{
    platform = {{
      source  = "platform.ai/platform"
      version = ">= 0.1.0"
    }}
  }}
}}

provider "platform" {{
  endpoint   = var.platform_endpoint
  api_key    = var.platform_api_key
  namespace  = "{namespace}"
}}
'''
    (output_dir / "provider.tf").write_text(provider_tf.strip())

    variables_tf = """
variable "platform_endpoint" {
  type    = string
  default = "http://localhost:8080"
}

variable "platform_api_key" {
  type      = string
  sensitive = true
  default   = ""
}
"""
    (output_dir / "variables.tf").write_text(variables_tf.strip())

    count = 0
    for ver in published:
        if not ver.kind or not ver.name:
            continue
        tf_name = ver.name.replace("-", "_")
        resource_type = f"platform_{ver.kind.lower()}"
        spec_json = json.dumps(ver.spec_json, indent=2)
        hcl = f'''
resource "{resource_type}" "{tf_name}" {{
  namespace = "{namespace}"
  version   = "{ver.version}"
  spec      = jsonencode({spec_json})
}}
'''
        (output_dir / f"{resource_type}_{tf_name}.tf").write_text(hcl.strip())
        count += 1

    (output_dir / "exported.json").write_text(
        export_terraform_json(published, namespace)
    )
    return count
