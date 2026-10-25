"""Bounded expression parser: never evaluate arbitrary Python from a question."""

from __future__ import annotations

import ast
import multiprocessing


def parse_expression(text: str, symbol_names: list[str]):
    import sympy as sp
    if len(text) > 2000:
        raise ValueError("Expression is too long")
    constants = {"pi": sp.pi, "E": sp.E}
    functions = {name: getattr(sp, name) for name in (
        "sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh", "tanh",
        "exp", "log", "sqrt", "Abs", "diff", "integrate")}
    if any(not name.isidentifier() or name.startswith("_") or name in constants or name in functions
           for name in symbol_names):
        raise ValueError("Invalid/reserved symbol name")
    names = {name: sp.Symbol(name, real=True) for name in symbol_names} | constants
    tree = ast.parse(text, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 250:
        raise ValueError("Expression is too complex")

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            if abs(node.value) > 10**100:
                raise ValueError("Numeric literal exceeds supported size")
            return sp.sympify(str(node.value))
        if isinstance(node, ast.Name) and node.id in names:
            return names[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add): return left + right
            if isinstance(node.op, ast.Sub): return left - right
            if isinstance(node.op, ast.Mult): return left * right
            if isinstance(node.op, ast.Div): return left / right
            if isinstance(node.op, ast.Pow):
                if right.is_number and abs(right) > 100:
                    raise ValueError("Exponent exceeds supported range")
                return left ** right
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in functions and not node.keywords and 1 <= len(node.args) <= 3:
                return functions[node.func.id](*(visit(x) for x in node.args))
        raise ValueError("Unsupported expression; use declared symbols and allowed arithmetic/functions")

    return visit(tree.body)


def _worker(left, right, symbols, connection):
    try:
        import sympy as sp
        lhs, rhs = parse_expression(left, symbols), parse_expression(right, symbols)
        difference = sp.simplify(lhs - rhs)
        equivalent = True if difference == 0 else False if difference.is_zero is False else None
        connection.send({"equivalent": equivalent, "difference": str(difference),
                         "assumptions": "Declared symbols are real; domain and units require separate review."})
    except Exception as error:
        connection.send({"error": str(error)})
    finally:
        connection.close()


def check_equivalence(left: str, right: str, symbols: list[str] | None = None,
                      timeout: float = 8) -> dict:
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(left, right, symbols or [], child), daemon=True)
    process.start()
    child.close()
    try:
        if not parent.poll(timeout):
            return {"equivalent": None, "error": "Symbolic check timed out; review manually"}
        result = parent.recv()
        if "error" in result:
            raise ValueError(result["error"])
        return result
    finally:
        if process.is_alive():
            process.terminate()
        process.join(2)
        parent.close()
