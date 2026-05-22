from hyground.index import WorkspaceIndex
from hyground.semantic import encode_semantic_tokens, semantic_tokens

URI = "file:///workspace/main.hy"


def test_semantic_tokens_classify_hy_source() -> None:
    source = '(defn foo [x]\n  "Docs"\n  (+ x 1)) ; comment\n#bang\n'
    index = WorkspaceIndex()
    index.update_document(URI, '(defreader bang [] 1)\n(defn foo [x]\n  "Docs"\n  (+ x 1))\n')

    tokens = semantic_tokens(source, lambda name, line, character: index.resolve(URI, name, line, character))
    by_text = {(source.splitlines()[token.line][token.start : token.start + token.length], token.token_type) for token in tokens}

    assert ("defn", "keyword") in by_text
    assert ("foo", "function") in by_text
    assert ('"Docs"', "string") in by_text
    assert ("+", "keyword") in by_text or ("+", "operator") in by_text
    assert ("1", "number") in by_text
    assert ("; comment", "comment") in by_text
    assert ("#bang", "macro") in by_text


def test_semantic_token_encoding_is_relative() -> None:
    data = encode_semantic_tokens(semantic_tokens("1\n  2\n", lambda name, line, character: None))

    assert data == [0, 0, 1, 7, 0, 1, 2, 1, 7, 0]


def test_semantic_tokens_resolve_parameters_with_position() -> None:
    source = "(defn foo [print]\n  (print 1))\n(print \"outside\")\n"
    index = WorkspaceIndex()
    index.update_document(URI, source)

    tokens = semantic_tokens(source, lambda name, line, character: index.resolve(URI, name, line, character))
    print_tokens = [
        token
        for token in tokens
        if source.splitlines()[token.line][token.start : token.start + token.length] == "print"
    ]

    assert [token.token_type for token in print_tokens] == ["variable", "variable", "function"]
