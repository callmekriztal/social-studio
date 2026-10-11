from typing import Optional, Tuple

from app.models.enums import VariantStatus
from app.models.variant import Variant
from app.validators.validator import VariantValidator


class SchedulingEligibilityError(Exception):
    """Raised when an unapproved or invalid variant is submitted for scheduling."""
    pass


class SchedulingEligibilityGuard:
    """Enforces the critical boundary that only valid, approved variants can be scheduled."""

    @classmethod
    def check_eligibility(cls, variant: Variant) -> Tuple[bool, Optional[str]]:
        """Check whether a variant is currently eligible for scheduling.

        Returns:
            Tuple of (is_eligible, explanation_if_ineligible).
        """
        # Rule 1: A draft variant cannot pass the guard
        if variant.status == VariantStatus.DRAFT:
            return False, "Variant is in DRAFT status. Only APPROVED variants may be scheduled."

        # Rule 2: A rejected variant cannot pass the guard
        if variant.status == VariantStatus.REJECTED:
            reason = variant.rejection_reason or "Constraint or editorial rejection"
            return False, f"Variant is REJECTED ({reason}) and cannot be scheduled."

        # Rule 3: A published variant cannot be scheduled again
        if variant.status == VariantStatus.PUBLISHED:
            return False, "Variant has already been PUBLISHED and cannot be scheduled again."

        # Rule 4: Must strictly be APPROVED
        if variant.status != VariantStatus.APPROVED:
            return False, f"Variant status '{variant.status.value}' is not eligible for scheduling."

        # Rule 5: An invalid approved variant cannot pass the guard
        validation = VariantValidator.validate(variant.platform, variant.content)
        if not validation.is_valid:
            errors_str = "; ".join(validation.errors)
            return (
                False,
                f"Approved variant violates platform constraints and cannot be scheduled: {errors_str}",
            )

        # Only a valid approved variant can pass
        return True, None

    @classmethod
    def assert_eligible_for_scheduling(cls, variant: Variant) -> None:
        """Assert scheduling eligibility or raise SchedulingEligibilityError.

        Guarantees that scheduling workers or future endpoints cannot bypass this rule.
        """
        is_eligible, explanation = cls.check_eligibility(variant)
        if not is_eligible:
            raise SchedulingEligibilityError(explanation)
