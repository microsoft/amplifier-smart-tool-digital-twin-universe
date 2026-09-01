"""Amplifier Digital Twin Universe, a smart tool.

The library is the tool. Every capability lives here; the CLI and any other
surface are thin adapters over this package and add nothing of their own.

Nothing in this module requires a model provider at import time. Model-backed
capabilities fail loudly, naming the remedy, only when actually invoked.
"""

from amplifier_digital_twin_universe.create import ProfileDraft, create_profile
from amplifier_digital_twin_universe.doctor import Diagnosis, Finding, diagnose, gather_evidence
from amplifier_digital_twin_universe.environments import (
    check_readiness,
    destroy,
    launch,
    list_environments,
    pull_files,
    push_files,
    run,
    status,
    update,
)
from amplifier_digital_twin_universe.errors import (
    EnvironmentLimitError,
    EnvironmentNotFoundError,
    GenerationFailedError,
    MissingPrerequisiteError,
    NoProviderError,
    OperationFailedError,
    OperationTimedOutError,
    ProfileInvalidError,
    ProfileNotFoundError,
    SmartToolError,
)
from amplifier_digital_twin_universe.install import InstallPlan, InstallStep, plan_install
from amplifier_digital_twin_universe.intelligence import Intelligence, ModelRequest, ModelResult, default_intelligence
from amplifier_digital_twin_universe.manager import ACTIONS, ManagePlan, PlannedStep, manage
from amplifier_digital_twin_universe.manifest import Manifest, Requirement, load_manifest
from amplifier_digital_twin_universe.prerequisites import HostReport, Prerequisite, probe
from amplifier_digital_twin_universe.profiles import ValidationReport, validate_profile

__all__ = [
    "ACTIONS",
    "MODEL_BACKED_CAPABILITIES",
    "Diagnosis",
    "EnvironmentLimitError",
    "EnvironmentNotFoundError",
    "Finding",
    "GenerationFailedError",
    "HostReport",
    "InstallPlan",
    "InstallStep",
    "Intelligence",
    "ManagePlan",
    "Manifest",
    "MissingPrerequisiteError",
    "ModelRequest",
    "ModelResult",
    "NoProviderError",
    "OperationFailedError",
    "OperationTimedOutError",
    "PlannedStep",
    "Prerequisite",
    "ProfileDraft",
    "ProfileInvalidError",
    "ProfileNotFoundError",
    "Requirement",
    "SmartToolError",
    "ValidationReport",
    "check_readiness",
    "create_profile",
    "default_intelligence",
    "destroy",
    "diagnose",
    "gather_evidence",
    "launch",
    "list_environments",
    "load_manifest",
    "manage",
    "plan_install",
    "probe",
    "pull_files",
    "push_files",
    "run",
    "status",
    "update",
    "validate_profile",
]

# Which capabilities consult a model. Declared here so a caller can read it
# programmatically and make cost and determinism decisions before invoking.
# Everything not listed is deterministic and runs with no provider configured.
MODEL_BACKED_CAPABILITIES: tuple[str, ...] = ("create_profile", "diagnose", "manage", "plan_install")
