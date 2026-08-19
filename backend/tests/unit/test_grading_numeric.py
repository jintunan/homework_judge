from homework_judge.grading.numeric import VerificationStatus, verify_numeric_equivalence


def test_numeric_verifier_supports_units_and_scientific_notation() -> None:
    assert verify_numeric_equivalence("100 cm", "1 m").status is VerificationStatus.EQUIVALENT
    assert verify_numeric_equivalence("1×10^3 V", "1000 V").status is VerificationStatus.EQUIVALENT


def test_numeric_verifier_rejects_different_values_and_dimensions() -> None:
    assert verify_numeric_equivalence("2 m", "1 m").status is VerificationStatus.NOT_EQUIVALENT
    assert verify_numeric_equivalence("1 s", "1 m").status is VerificationStatus.UNABLE


def test_numeric_verifier_does_not_guess_missing_or_unknown_units() -> None:
    assert verify_numeric_equivalence("1", "1 m").status is VerificationStatus.UNABLE
    assert verify_numeric_equivalence("1 mystery", "1 m").status is VerificationStatus.UNABLE
