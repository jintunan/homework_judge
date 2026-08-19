from homework_judge.grading.formula import verify_formula_equivalence
from homework_judge.grading.numeric import VerificationStatus


def test_formula_verifier_accepts_common_equivalent_forms() -> None:
    expanded = verify_formula_equivalence("(x+1)^2", "x^2+2*x+1")
    factored = verify_formula_equivalence("x*(x+1)", "x^2+x")
    assert expanded.status is VerificationStatus.EQUIVALENT
    assert factored.status is VerificationStatus.EQUIVALENT


def test_formula_verifier_rejects_non_equivalent_form() -> None:
    assert verify_formula_equivalence("x+1", "x+2").status is VerificationStatus.NOT_EQUIVALENT


def test_formula_verifier_safely_rejects_unsupported_input() -> None:
    assert verify_formula_equivalence("__import__('os')", "1").status is VerificationStatus.UNABLE
    assert verify_formula_equivalence("x" * 400, "x").status is VerificationStatus.UNABLE
