from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from .service import CaptureStore, MiningRun, SalienceRecord, _new_uuid7


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class MiningPreparation:
    run_key: str
    salience_id: str
    scope_id: str
    generation: int
    stage: str
    miner_version: str
    config_fingerprint: str
    input_fingerprint: str
    parent_run_id: str | None
    prior_output: dict
    context: list[dict]
    delta: list[dict]
    covered_capture_ids: list[str]
    evidence_eligible_capture_ids: list[str]
    reusable_run: MiningRun | None = None


class MiningRunCoordinator:
    """Prepare incremental miner input and persist successful reusable output."""

    def __init__(self, store: CaptureStore, *, context_turns: int = 4) -> None:
        if context_turns < 1:
            raise ValueError("context_turns must be at least 1")
        self.store = store
        self.context_turns = context_turns

    def prepare(
        self,
        salience: SalienceRecord,
        *,
        stage: str,
        miner_version: str,
        config: dict,
    ) -> MiningPreparation:
        if salience.status != "admitted":
            raise ValueError("only admitted salience records can enter mining")
        if not stage or not miner_version:
            raise ValueError("stage and miner_version are required")

        config_fingerprint = _fingerprint(config)
        parent = self.store.latest_successful_mining_run(
            salience.scope_id,
            before_generation=salience.generation,
            stage=stage,
            miner_version=miner_version,
            config_fingerprint=config_fingerprint,
        )
        previously_covered = set(parent.covered_capture_ids if parent else [])
        delta = [
            item for item in salience.trimmed
            if item["capture_id"] not in previously_covered
        ]
        delta_ids = {item["capture_id"] for item in delta}
        first_delta_index = next(
            (index for index, item in enumerate(salience.trimmed) if item["capture_id"] in delta_ids),
            len(salience.trimmed),
        )
        preceding = [
            item for item in salience.trimmed[:first_delta_index]
            if item["capture_id"] in previously_covered
        ]
        if not preceding and parent is not None:
            preceding = [
                item for item in salience.trimmed
                if item["capture_id"] in previously_covered
            ]
        context = preceding[-self.context_turns:]
        ordered_covered = [item["capture_id"] for item in salience.trimmed]
        input_payload = {
            "salience_id": salience.salience_id,
            "stage": stage,
            "miner_version": miner_version,
            "config_fingerprint": config_fingerprint,
            "parent_run_id": parent.run_id if parent else None,
            "prior_output_sha256": _fingerprint(parent.output) if parent else None,
            "context": [
                {"capture_id": item["capture_id"], "content_sha256": _fingerprint(item["content"])}
                for item in context
            ],
            "delta": [
                {"capture_id": item["capture_id"], "content_sha256": _fingerprint(item["content"])}
                for item in delta
            ],
        }
        input_fingerprint = _fingerprint(input_payload)
        run_key = _fingerprint({
            "salience_id": salience.salience_id,
            "stage": stage,
            "miner_version": miner_version,
            "config_fingerprint": config_fingerprint,
            "input_fingerprint": input_fingerprint,
        })
        reusable = self.store.get_mining_run(run_key)
        return MiningPreparation(
            run_key=run_key,
            salience_id=salience.salience_id,
            scope_id=salience.scope_id,
            generation=salience.generation,
            stage=stage,
            miner_version=miner_version,
            config_fingerprint=config_fingerprint,
            input_fingerprint=input_fingerprint,
            parent_run_id=parent.run_id if parent else None,
            prior_output=parent.output if parent else {},
            context=context,
            delta=delta,
            covered_capture_ids=ordered_covered,
            evidence_eligible_capture_ids=[item["capture_id"] for item in delta],
            reusable_run=reusable if reusable and reusable.status == "succeeded" else None,
        )

    def record_success(
        self,
        preparation: MiningPreparation,
        *,
        output: dict,
        evidence_capture_ids: list[str],
        now: datetime | None = None,
    ) -> MiningRun:
        eligible = set(preparation.evidence_eligible_capture_ids)
        invalid = sorted(set(evidence_capture_ids) - eligible)
        if invalid:
            raise ValueError(
                "context-only captures cannot become new evidence: " + ", ".join(invalid)
            )
        completed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        run = MiningRun(
            run_id=_new_uuid7(),
            run_key=preparation.run_key,
            salience_id=preparation.salience_id,
            scope_id=preparation.scope_id,
            generation=preparation.generation,
            stage=preparation.stage,
            miner_version=preparation.miner_version,
            config_fingerprint=preparation.config_fingerprint,
            input_fingerprint=preparation.input_fingerprint,
            parent_run_id=preparation.parent_run_id,
            status="succeeded",
            output=output,
            covered_capture_ids=preparation.covered_capture_ids,
            evidence_capture_ids=list(dict.fromkeys(evidence_capture_ids)),
            error=None,
            created_at=completed_at,
            completed_at=completed_at,
        )
        return self.store.put_mining_run(run)

    def record_failure(
        self,
        preparation: MiningPreparation,
        *,
        error: str,
        now: datetime | None = None,
    ) -> MiningRun:
        completed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        run = MiningRun(
            run_id=_new_uuid7(),
            run_key=preparation.run_key,
            salience_id=preparation.salience_id,
            scope_id=preparation.scope_id,
            generation=preparation.generation,
            stage=preparation.stage,
            miner_version=preparation.miner_version,
            config_fingerprint=preparation.config_fingerprint,
            input_fingerprint=preparation.input_fingerprint,
            parent_run_id=preparation.parent_run_id,
            status="failed",
            output={},
            covered_capture_ids=[],
            evidence_capture_ids=[],
            error=error[:1000],
            created_at=completed_at,
            completed_at=completed_at,
        )
        return self.store.put_mining_run(run)
