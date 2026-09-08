"""Read the three note layers out of a description somebody already typed.

The notes were written into descriptions long before they had fields of their
own, in whatever wording was to hand. The shop's own data uses all of these:

    Cabeça: lírio-do-vale, passiflora          label and colon
    Notas de cabeça - Maçã, Bergamota          label and dash
    Notas de topo Bergamota, menta             label and nothing at all
    notas de topo são: Açafrão e Jasmim        label, verb, colon
    Notas de cabeça (topo):                    label, aside, value on the
    Pistache fresco                              lines beneath
    as notas de saída são Canela e Gengibre    the manufacturer's prose

and each layer answers to several names: topo, saída, cabeça, abertura for the
first; coração, corpo, centro for the second; base, fundo for the third.

Two things it will not do, because a wrong note on a listing is worse than a
missing one. It refuses a value that reads as a sentence about the notes
rather than the notes themselves. And it cuts the descriptive tail off a value
that has one - "Abacaxi e crème brûlée, proporcionando uma abertura doce" is
two notes and a flourish, and only the notes belong in the field.
"""
import re

# Every word the shop has used for each layer, in its own writing.
LAYER_WORDS = {
    'top': ['topo', 'saída', 'saida', 'saídas', 'saidas', 'cabeça', 'cabeca',
            'abertura'],
    'heart': ['coração', 'coracao', 'corpo', 'centro', 'meio'],
    'base': ['base', 'fundo', 'fundos'],
}

# A value that reads as a sentence about the notes rather than a list of them.
PROSE_MARKERS = re.compile(
    r'\b(é|são|foi|apresenta|marcada|marcado|combina|abre|revela|encontram|'
    r'envolve|desde|esta|este)\b', re.IGNORECASE)

# "Bergamota, menta, proporcionando uma abertura fresca" - the notes stop
# where the flourish starts.
TAIL_CLAUSE = re.compile(
    r'[,;]\s*(?:proporcionando|conferindo|oferecendo|criando|trazendo|'
    r'deixando|garantindo|acrescentando|resultando|dando|formando|'
    r'que\s|com\s+um|para\s+um)',
    re.IGNORECASE)

SEPARATOR = r'(?:\s*\([^)]*\))?\s*(?:são|sao|é)?\s*[:：\-–—]'
MAX_LENGTH = 200


def _clean(value):
    value = re.sub(r'\s+', ' ', (value or '')).strip()
    cut = TAIL_CLAUSE.search(value)
    if cut:
        value = value[:cut.start()]
    return value.strip(' .;:,-–—')[:MAX_LENGTH].strip()


def _usable(value):
    """Notes are a short list of ingredients, not a sentence about them."""
    if not value or len(value) < 2:
        return False
    if PROSE_MARKERS.search(value):
        return False
    return len(value) <= 120


def _value_after(text, end):
    """What follows a label: the rest of its line, or the lines beneath it.

    ``Notas de cabeça (topo):`` puts its notes on the next line, so an empty
    remainder means look down rather than give up.
    """
    line_end = text.find('\n', end)
    same_line = text[end:line_end if line_end != -1 else len(text)]
    if _clean(same_line):
        return same_line

    if line_end == -1:
        return ''
    following = []
    for line in text[line_end + 1:].split('\n'):
        stripped = line.strip()
        if not stripped:
            break                      # blank line ends the block
        if re.match(r'notas?\b', stripped, re.IGNORECASE):
            break                      # the next layer's label
        following.append(stripped)
        if len(following) >= 4:
            break
    return ', '.join(following)


def _find(text, words):
    alternatives = '|'.join(re.escape(word) for word in words)

    # With a separator, the label may stand on its own ("Base: ...").
    with_separator = re.compile(
        r'(?:notas?\s+(?:de\s+|da\s+)?)?(?:' + alternatives + r')' + SEPARATOR,
        re.IGNORECASE)
    for match in with_separator.finditer(text):
        candidate = _clean(_value_after(text, match.end()))
        if _usable(candidate):
            return candidate

    # Without one, require the word "notas" - otherwise a stray "base" in the
    # marketing copy would be read as a note.
    # The verb is optional and dropped, so "as notas de saida sao Canela e
    # Gengibre" yields just the notes, and the value stops at a semicolon
    # because the blurb runs all three layers into a single sentence.
    bare = re.compile(
        r'notas?\s+(?:de\s+|da\s+)?(?:' + alternatives + r')\s+'
        r'(?:s[\u00e3a]o|[\u00e9e])?\s*([^\n;.]+)',
        re.IGNORECASE)
    for match in bare.finditer(text):
        candidate = _clean(match.group(1))
        if _usable(candidate):
            return candidate
    return ''


def parse_notes(description):
    """``{'top': ..., 'heart': ..., 'base': ...}`` - empty where unreadable."""
    text = (description or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not text:
        return {'top': '', 'heart': '', 'base': ''}
    return {layer: _find(text, words) for layer, words in LAYER_WORDS.items()}
