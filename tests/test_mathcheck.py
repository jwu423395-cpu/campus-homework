import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from campus_homework.mathcheck import check_equivalence, parse_expression


class MathTests(unittest.TestCase):
    def test_harmonic_derivative(self):
        result = check_equivalence("diff(A*cos(w*t+p),t)", "-A*w*sin(w*t+p)", ["A", "w", "t", "p"])
        self.assertIs(result["equivalent"], True)

    def test_negative_sign_error(self):
        result = check_equivalence("-4", "4")
        self.assertIs(result["equivalent"], False)

    def test_integral(self):
        result = check_equivalence("integrate(2*x,x)", "x**2", ["x"])
        self.assertIs(result["equivalent"], True)

    def test_code_injection_rejected(self):
        for value in ("__import__('os').getcwd()", "x.__class__", "open('file')", "[x for x in (1,2)]"):
            with self.assertRaises(ValueError): parse_expression(value, ["x"])

    def test_unbounded_expression_rejected(self):
        with self.assertRaises(ValueError): parse_expression("2**1000000", [])


if __name__ == "__main__":
    unittest.main()
