"""Word counting (spec §4.2): whitespace tokens of the original text."""


def count_words(text: str) -> int:
    return len(text.split())
