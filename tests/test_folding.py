from hyground.folding import FoldRange, folding_ranges


def test_folding_ranges_for_multiline_forms() -> None:
    source = "(defn foo [x]\n  (if x\n    [1\n     2]\n    0))\n"

    ranges = folding_ranges(source)

    assert FoldRange(0, 0, 4, 7) in ranges
    assert FoldRange(1, 2, 4, 6) in ranges
    assert FoldRange(2, 4, 3, 7) in ranges


def test_folding_ignores_strings_and_comments() -> None:
    source = '(print "(") ; [\n(defn foo []\n  1)\n'

    ranges = folding_ranges(source)

    assert ranges == [FoldRange(1, 0, 2, 4)]


def test_folding_tolerates_incomplete_buffers() -> None:
    source = "(defn foo []\n  (print 1)\n"

    ranges = folding_ranges(source)

    assert ranges == []
