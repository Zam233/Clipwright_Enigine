"""Persona 验证器。"""

from __future__ import annotations

from clipwright.schema.persona import PersonaManifest


class PersonaValidationError(Exception):
    """Persona 验证失败。"""


def validate_manifest(manifest: PersonaManifest) -> list[str]:
    """验证 Persona manifest 的完整性，返回警告列表。"""
    warnings: list[str] = []

    if not manifest.persona_id:
        warnings.append("persona_id is required")

    if not manifest.parameter:
        warnings.append("parameter layer is missing — at least a parameter layer is required")

    param = manifest.parameter
    if param is not None:
        identity = param.identity
        if not identity.tone:
            warnings.append("identity.tone is empty")

        constraints = param.constraints
        if constraints.max_duration_sec < constraints.min_duration_sec:
            warnings.append(
                f"max_duration_sec ({constraints.max_duration_sec}) < "
                f"min_duration_sec ({constraints.min_duration_sec})"
            )

    return warnings
