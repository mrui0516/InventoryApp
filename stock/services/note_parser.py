"""Read the three note layers out of a description somebody already typed.

The notes were written into the description long before they had fields of
their own, in whatever wording was to hand. Four shapes appear in the shop's
own data:

    Saída: Gengibre, Canela, Cardamomo
    Notas de Cabeça: Rose milk;
    Notas de Topo: A abertura é marcada pela bergamota...
    ...as notas de saída são Canela e Gengibre; as notas de coração são...

and each layer goes by several names - topo, saída, cabeça, abertura for the
first; coração, corpo, centro for the second; base, fundo for the third.

This reads them so nobody has to copy several hundred products by hand. It is
deliberately cautious: anything it cannot read confidently is left alone and
reported, because a wrong note on a listing is worse than a missing one.
"""
import re

# Every word the shop has used for each layer, in its own writing.
LAYER_WORDS = {
    'top': ['topo', 'saída', 'saida', 'cabeça', 'cabeca', 'abertura', 'saidas', 'saídas'],
    'heart': ['coração', 'coracao', 'corpo', 'centro', 'meio'],
    'base': ['base', 'fundo', 'fundos'],
}

# A value that reads as a sentence rather than a list of notes. "Notas de Topo:
# A abertura do Afeef é marcada pela bergamota" is prose about the notes, not
# the notes - taking it would put a paragraph in a field meant for three words.
PROSE_MARKERS = re.compile(
    r'\b(é|são|foi|apresenta|marcada|marcado|oferece|combina|abre|revela|'
    r'encontram|proporciona|traz)\b', re.IGNORECASE)

MAX_LENGTH = 200


def _clean(value):
    value = re.sub(r'\s+', ' ', (value or '')).strip()
    value = value.strip(' .;:,-–—')
    return value[:MAX_LENGTH].strip()


def _looks_like_a_list(value):
    """Notes are a short list of ingredients, not a sentence about them."""
    if not value or len(value) < 2:
        return False
    if PROSE_MARKERS.search(value):
        return False
    # A run of words with no separator at all is usually a fragment of prose.
    return len(value) <= 120


def _labelled(description, words):
    """``Notas de Topo: Bergamota, Pêssego`` and its many spellings."""
    alternatives = '|'.join(re.escape(word) for word in words)
    pattern = re.compile(
        r'(?:notas?\s+(?:de\s+|da\s+)?)?(?:' + alternatives + r')\s*[:：]\s*([^\n;]+)',
        re.IGNORECASE)
    for match in pattern.finditer(description):
        candidate = _clean(match.group(1))
        if _looks_like_a_list(candidate):
            return candidate
    return ''


def _prose(description, words):
    """``as notas de saída são Canela, Cardamomo e Gengibre``."""
    alternatives = '|'.join(re.escape(word) for word in words)
    pattern = re.compile(
        r'notas?\s+de\s+(?:' + alternatives + r')\s+(?:são|sao|é|e)\s+([^;.\n]+)',
        re.IGNORECASE)
    match = pattern.search(description)
    if not match:
        return ''
    candidate = _clean(match.group(1))
    return candidate if _looks_like_a_list(candidate) else ''


def parse_notes(description):
    """``{'top': ..., 'heart': ..., 'base': ...}`` - empty where unreadable."""
    text = (description or '').strip()
    if not text:
        return {'top': '', 'heart': '', 'base': ''}

    found = {}
    for layer, words in LAYER_WORDS.items():
        # The labelled form is the shop's own writing and wins; the prose form
        # is the manufacturer blurb underneath it and is the fallback.
        found[layer] = _labelled(text, words) or _prose(text, words)
    return found
