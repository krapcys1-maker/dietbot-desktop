"""Niezależny weryfikator planów (nie importuje silnika)."""

from .verify import Issue, Severity, VerificationReport, verify_plan

__all__ = ["Issue", "Severity", "VerificationReport", "verify_plan"]
