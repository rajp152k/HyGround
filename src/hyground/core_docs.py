"""Hy core form docs.

Hy compiler forms are not ordinary runtime Python values. Many are pattern macro
wrappers with no useful ``__doc__``. This module is deliberately explicit: it is
small, versionable, and can later be generated from Hy's Sphinx inventory.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CoreDoc:
    signature: str
    documentation: str


CORE_DOCS: dict[str, CoreDoc] = {
    "if": CoreDoc(
        "(if test true-value false-value)",
        "Evaluate TEST. If it is truthy, evaluate and return TRUE-VALUE; otherwise evaluate and return FALSE-VALUE. Use (do ...) when a branch needs multiple forms.",
    ),
    "do": CoreDoc(
        "(do body...)",
        "Evaluate BODY forms in order and return the value of the last form.",
    ),
    "setv": CoreDoc(
        "(setv target value ...)",
        "Assign values to targets. Accepts one or more TARGET VALUE pairs.",
    ),
    "setx": CoreDoc(
        "(setx target value)",
        "Assignment expression form. Assign VALUE to TARGET and return the assigned value.",
    ),
    "defn": CoreDoc(
        "(defn name [params] body...)",
        "Define a function. A string literal at the start of BODY becomes the function docstring.",
    ),
    "fn": CoreDoc(
        "(fn [params] body...)",
        "Create an anonymous function.",
    ),
    "defclass": CoreDoc(
        "(defclass name [bases...] body...)",
        "Define a Python class. A string literal at the start of BODY becomes the class docstring.",
    ),
    "defmacro": CoreDoc(
        "(defmacro name [params] body...)",
        "Define a Hy macro in the current macro scope.",
    ),
    "import": CoreDoc(
        "(import module...)",
        "Import Python modules, names, or aliases into the current namespace.",
    ),
    "require": CoreDoc(
        "(require module...)",
        "Import Hy macros so they can be used at compile time.",
    ),
    "for": CoreDoc(
        "(for [clauses] body...)",
        "Python-style for loop. The bracketed clauses have the same shape as lfor clauses. BODY forms are evaluated for side effects; the form returns None.",
    ),
    "lfor": CoreDoc(
        "(lfor clauses value)",
        "List comprehension. CLAUSES can include LVALUE ITERABLE, :async LVALUE ITERABLE, :do FORM, :setv LVALUE RVALUE, and :if CONDITION. VALUE is accumulated into a list.",
    ),
    "dfor": CoreDoc(
        "(dfor clauses key value)",
        "Dictionary comprehension. Like lfor, but each iteration evaluates KEY and VALUE and accumulates them into a dictionary.",
    ),
    "sfor": CoreDoc(
        "(sfor clauses value)",
        "Set comprehension. Like lfor, but accumulates results into a set.",
    ),
    "gfor": CoreDoc(
        "(gfor clauses value)",
        "Generator comprehension. Like lfor, but returns a generator instead of immediately building a list.",
    ),
    "when": CoreDoc(
        "(when test body...)",
        "Shorthand for (if test (do body...) None).",
    ),
    "cond": CoreDoc(
        "(cond test result ...)",
        "Shorthand for nested if forms. Tests and results are paired; returns None if no test matches unless you provide a final True fallback.",
    ),
    "while": CoreDoc(
        "(while condition body...)",
        "Loop while CONDITION is truthy. BODY forms are evaluated for side effects and the form returns None.",
    ),
    "try": CoreDoc(
        "(try body... (except ... ) (else ... ) (finally ...))",
        "Exception-handling form corresponding to Python try/except/else/finally.",
    ),
    "with": CoreDoc(
        "(with [context target ...] body...)",
        "Context-manager form corresponding to Python with. Evaluates BODY within the dynamic extent of the context managers.",
    ),
    "match": CoreDoc(
        "(match subject case...)",
        "Structural pattern matching form corresponding to Python match/case.",
    ),
    "return": CoreDoc("(return value)", "Return from the nearest enclosing function."),
    "yield": CoreDoc("(yield value)", "Yield a value from a generator function."),
    "await": CoreDoc("(await awaitable)", "Await an awaitable object in an async context."),
}
