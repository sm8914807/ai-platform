"""Config bundle compiler and signer."""

import hashlib
import json
from datetime import datetime, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption, PublicFormat

from ai_platform.core.models import BundleManifest
from ai_platform.registry.store import ResourceVersionRecord


class BundleCompiler:
    """Compiles published resources into a signed runtime bundle."""

    def __init__(self, signing_key: Ed25519PrivateKey | None = None) -> None:
        if signing_key is None:
            signing_key = Ed25519PrivateKey.generate()
        self._private_key = signing_key
        self._public_key_bytes = signing_key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )

    @property
    def public_key_hex(self) -> str:
        return self._private_key.public_key().public_bytes_raw().hex()

    def compile(
        self,
        namespace: str,
        environment: str,
        published: list[ResourceVersionRecord],
    ) -> BundleManifest:
        resources: list[dict] = []
        for v in published:
            resources.append(
                {
                    "kind": v.kind,
                    "name": v.name,
                    "version": v.version,
                    "spec": v.spec_json,
                    "status": v.status_json,
                }
            )
        resources.sort(key=lambda r: (r.get("kind", ""), r.get("name", "")))

        payload = {
            "namespace": namespace,
            "environment": environment,
            "resources": resources,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        bundle_hash = f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"
        signature = self._private_key.sign(canonical.encode()).hex()

        return BundleManifest(
            namespace=namespace,
            environment=environment,
            bundle_hash=bundle_hash,
            signature=signature,
            resources=resources,
            created_at=datetime.now(timezone.utc),
        )

    def verify(self, manifest: BundleManifest, public_key_bytes: bytes) -> bool:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        payload = {
            "namespace": manifest.namespace,
            "environment": manifest.environment,
            "resources": sorted(
                manifest.resources,
                key=lambda r: (r.get("kind", ""), r.get("name", "")),
            ),
            "createdAt": manifest.created_at.isoformat(),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        expected_hash = f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"
        if manifest.bundle_hash != expected_hash:
            return False
        pub = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        try:
            pub.verify(bytes.fromhex(manifest.signature), canonical.encode())
            return True
        except Exception:
            return False

    def export_signing_key_pem(self) -> bytes:
        return self._private_key.private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        )
