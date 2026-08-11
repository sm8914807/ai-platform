"""Pre-built compliance packs — HIPAA, PCI, GDPR, SOC2."""

from ai_platform.core.models import CompliancePack, PlatformResource, ResourceKind, ResourceMetadata
from ai_platform.registry.store import RegistryStore

BUILTIN_PACKS: list[CompliancePack] = [
    CompliancePack(
        id="hipaa-baseline",
        name="HIPAA Baseline",
        framework="HIPAA",
        version="1.0.0",
        description="PII masking, audit policies, and data residency guardrails for HIPAA.",
        resources=[
            {
                "kind": "Guardrail",
                "metadata": {"name": "hipaa-pii-mask", "version": "1.0.0"},
                "spec": {
                    "type": "pii_mask",
                    "config": {
                        "entities": ["email", "phone", "credit_card"],
                        "action": "mask",
                    },
                },
            },
            {
                "kind": "Policy",
                "metadata": {"name": "hipaa-audit-policy", "version": "1.0.0"},
                "spec": {
                    "rules": [
                        {
                            "effect": "allow",
                            "principals": ["team:healthcare"],
                            "actions": ["agent:run", "memory:read"],
                            "resources": ["agents/*"],
                            "conditions": {"dataResidency": "us"},
                        }
                    ],
                },
            },
            {
                "kind": "Guardrail",
                "metadata": {"name": "hipaa-injection-detect", "version": "1.0.0"},
                "spec": {"type": "injection_detect", "config": {"action": "block"}},
            },
        ],
    ),
    CompliancePack(
        id="pci-baseline",
        name="PCI Baseline",
        framework="PCI",
        version="1.0.0",
        description="Card data masking and restricted tool access for PCI workloads.",
        resources=[
            {
                "kind": "Guardrail",
                "metadata": {"name": "pci-card-mask", "version": "1.0.0"},
                "spec": {
                    "type": "pii_mask",
                    "config": {"entities": ["credit_card"], "action": "block"},
                },
            },
            {
                "kind": "Policy",
                "metadata": {"name": "pci-tool-restrict", "version": "1.0.0"},
                "spec": {
                    "rules": [
                        {
                            "effect": "deny",
                            "principals": ["*"],
                            "actions": ["tool:invoke"],
                            "resources": ["tools/payment-*"],
                            "conditions": {},
                        }
                    ],
                },
            },
        ],
    ),
    CompliancePack(
        id="gdpr-baseline",
        name="GDPR Baseline",
        framework="GDPR",
        version="1.0.0",
        description="EU data residency policy and PII controls for GDPR.",
        resources=[
            {
                "kind": "Policy",
                "metadata": {"name": "gdpr-residency", "version": "1.0.0"},
                "spec": {
                    "rules": [
                        {
                            "effect": "allow",
                            "principals": ["*"],
                            "actions": ["agent:run", "memory:write"],
                            "resources": ["*"],
                            "conditions": {"dataResidency": "eu"},
                        }
                    ],
                },
            },
            {
                "kind": "Guardrail",
                "metadata": {"name": "gdpr-pii-mask", "version": "1.0.0"},
                "spec": {
                    "type": "pii_mask",
                    "config": {"entities": ["email", "phone"], "action": "mask"},
                },
            },
        ],
    ),
    CompliancePack(
        id="soc2-baseline",
        name="SOC2 Baseline",
        framework="SOC2",
        version="1.0.0",
        description="Audit logging policy and injection detection for SOC2.",
        resources=[
            {
                "kind": "Policy",
                "metadata": {"name": "soc2-audit", "version": "1.0.0"},
                "spec": {
                    "rules": [
                        {
                            "effect": "allow",
                            "principals": ["team:platform-admins"],
                            "actions": ["resource:publish", "resource:*"],
                            "resources": ["*"],
                        }
                    ],
                },
            },
            {
                "kind": "Guardrail",
                "metadata": {"name": "soc2-injection", "version": "1.0.0"},
                "spec": {"type": "injection_detect", "config": {"action": "alert"}},
            },
        ],
    ),
]


class CompliancePackService:
    def __init__(self, registry: RegistryStore, db_path: str) -> None:
        self.registry = registry
        self.db_path = db_path
        self._packs = {p.id: p for p in BUILTIN_PACKS}

    def list_packs(self) -> list[CompliancePack]:
        return list(self._packs.values())

    def get_pack(self, pack_id: str) -> CompliancePack | None:
        return self._packs.get(pack_id)

    async def install_pack(
        self,
        pack_id: str,
        namespace_id: str,
        namespace_path: str,
        installed_by: str | None = None,
    ) -> dict[str, object]:
        pack = self.get_pack(pack_id)
        if not pack:
            raise ValueError(f"Compliance pack not found: {pack_id}")

        installed: list[str] = []
        for res_doc in pack.resources:
            kind = ResourceKind(res_doc["kind"])
            meta = res_doc["metadata"]
            resource = PlatformResource(
                kind=kind,
                metadata=ResourceMetadata(
                    name=meta["name"],
                    namespace=namespace_path,
                    version=meta.get("version", pack.version),
                ),
                spec=res_doc["spec"],
            )
            await self.registry.upsert_resource_version(
                namespace_id, resource, installed_by, f"compliance-pack:{pack_id}"
            )
            await self.registry.publish(
                namespace_id, kind, resource.metadata.name, resource.metadata.version
            )
            installed.append(f"{kind.value}/{resource.metadata.name}")

        from datetime import datetime, timezone
        import aiosqlite
        from ai_platform.core.ids import new_id

        install_id = new_id("cmp")
        now = datetime.now(timezone.utc).isoformat()
        conn = await aiosqlite.connect(self.db_path)
        await conn.execute(
            "INSERT OR IGNORE INTO compliance_installations "
            "(id, pack_id, namespace_id, installed_by, installed_at) VALUES (?, ?, ?, ?, ?)",
            (install_id, pack_id, namespace_id, installed_by, now),
        )
        await conn.commit()
        await conn.close()

        return {
            "installationId": install_id,
            "packId": pack_id,
            "framework": pack.framework,
            "resources": installed,
        }
