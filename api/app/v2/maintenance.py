from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import datetime, timezone

from .claims import RelationDefinition, RelationSchema
from .service import (
    CaptureStore,
    ExtensionRelation,
    ReminingRequest,
    _new_uuid7,
)


MAINTAINER_VERSION = "knowledge-maintainer-v1"


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


class KnowledgeMaintainer:
    """Own reviewable schema evolution and safe replacement of mined output."""

    def __init__(self, store: CaptureStore, core_schema: RelationSchema) -> None:
        self.store = store
        self.core_schema = core_schema

    def request_remining(
        self, source_run_id: str, reason: str, *, now: datetime | None = None
    ) -> ReminingRequest:
        source = self.store.get_mining_run_by_id(source_run_id)
        if source is None or source.status != "succeeded":
            raise ValueError("re-mining requires a successful source run")
        if not self.store.is_resolution_complete(source_run_id) or not self.store.is_claim_commit_complete(source_run_id):
            raise ValueError("re-mining requires a fully resolved and committed source run")
        reason = " ".join(reason.strip().split())
        if not reason:
            raise ValueError("re-mining reason is required")
        created_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        request_key = _fingerprint({"source_run_id": source_run_id, "reason": reason})
        return self.store.request_remining(ReminingRequest(
            request_id=_new_uuid7(), request_key=request_key, source_run_id=source_run_id,
            salience_id=source.salience_id, scope_id=source.scope_id, reason=reason,
            reprocess_token=_new_uuid7(), status="pending", replacement_run_id=None,
            created_at=created_at,
        ))

    def effective_relation(self, name: str) -> tuple[str, RelationDefinition, str] | None:
        core = self.core_schema.relations.get(name)
        if core is not None:
            return name, core, self.core_schema.schema_version
        extension = self.store.get_extension_relation(name)
        if extension is None or extension.status == "reverted":
            return None
        definition = RelationDefinition.model_validate(extension.definition)
        if extension.status == "promoted" and extension.promoted_relation:
            promoted = self.core_schema.relations.get(extension.promoted_relation)
            if promoted is None:
                raise ValueError("promoted extension points to an unknown core relation")
            return extension.promoted_relation, promoted, extension.schema_version
        return name, definition, extension.schema_version

    def admit_extension(
        self, name: str, definition: RelationDefinition, *, now: datetime | None = None
    ) -> ExtensionRelation:
        if not name.startswith("ext:"):
            raise ValueError("only ext:* relations can be admitted automatically")
        if self.store.get_extension_relation(name) is not None:
            return self.store.get_extension_relation(name)  # type: ignore[return-value]
        created_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        dumped = definition.model_dump(mode="json")
        version = f"ext-v1-{_fingerprint({'relation': name, 'definition': dumped})[:12]}"
        return self.store.put_extension_relation(ExtensionRelation(
            relation=name, definition=dumped, status="admitted", schema_version=version,
            promoted_relation=None, schema_commit=None,
            migration_plan={"strategy": "conservative_admission", "statefulness": "eventive", "cardinality": "many"},
            created_at=created_at, updated_at=created_at,
        ))

    def promote_extension(
        self, name: str, core_relation: str, schema_commit: str, *, now: datetime | None = None
    ) -> ExtensionRelation:
        extension = self.store.get_extension_relation(name)
        if (
            extension is not None and extension.status == "promoted"
            and extension.promoted_relation == core_relation
            and extension.schema_commit == schema_commit
        ):
            return extension
        if extension is None or extension.status == "reverted":
            raise ValueError("active extension relation not found")
        if extension.status == "promoted":
            raise ValueError("extension is already promoted; revert it before changing the target")
        if core_relation not in self.core_schema.relations:
            raise ValueError("promotion target must be a shipped core relation")
        if re.fullmatch(r"[0-9a-f]{7,64}", schema_commit) is None:
            raise ValueError("schema_commit must be a Git commit hash")
        source = RelationDefinition.model_validate(extension.definition)
        target = self.core_schema.relations[core_relation]
        if source.object_kind != target.object_kind:
            raise ValueError("promotion cannot change object kind without a separate migration")
        updated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        active_ids = [
            item.claim_id for item in self.store.list_claims(limit=250)
            if item.relation == name and item.lifecycle == "active"
        ]
        promoted = replace(
            extension, status="promoted", promoted_relation=core_relation,
            schema_commit=schema_commit, updated_at=updated_at,
            migration_plan={
                "strategy": "alias_then_reprocess",
                "from_relation": name,
                "to_relation": core_relation,
                "active_claim_ids": active_ids,
                "safety": "existing claims remain active until a successful scoped re-mining replaces them",
            },
        )
        return self.store.update_extension_relation(promoted)

    def revert_extension(
        self, name: str, schema_commit: str, *, now: datetime | None = None
    ) -> ExtensionRelation:
        extension = self.store.get_extension_relation(name)
        if extension is not None and extension.status == "reverted" and extension.schema_commit == schema_commit:
            return extension
        if extension is None or extension.status == "reverted":
            raise ValueError("active extension relation not found")
        if re.fullmatch(r"[0-9a-f]{7,64}", schema_commit) is None:
            raise ValueError("schema_commit must be a Git commit hash")
        updated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        affected = self.store.supersede_claims_for_relation(name)
        reverted = replace(
            extension, status="reverted", schema_commit=schema_commit, updated_at=updated_at,
            migration_plan={
                "strategy": "compensating_supersession",
                "relation": name,
                "superseded_claim_ids": affected,
                "destructive_delete": False,
            },
        )
        return self.store.update_extension_relation(reverted)
