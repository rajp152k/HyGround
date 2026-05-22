from hyground.word import enclosing_call, occurrences, word_at, word_prefix


def test_word_prefix_and_word_at() -> None:
    source = "(lfor x (range 3) x)\n"

    assert word_prefix(source, 0, 3) == "lf"
    assert word_at(source, 0, 2) == "lfor"


def test_occurrences() -> None:
    assert occurrences("(foo)\n(foo bar)\n", "foo") == [(0, 1, 4), (1, 1, 4)]


def test_enclosing_call() -> None:
    source = "(foo 1 (+ 2 3) \n"
    assert enclosing_call(source, 0, len(source)) == ("foo", 2)
