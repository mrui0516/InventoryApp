"""Scent facts written as labelled lines in a description: the three note
layers, the family, the reference and - in a pasted paragraph - the gender.

One reading, three callers (2026-11-21): the product form's quick import fills
the fields from a pasted paragraph; saving the form, and migration 0096 once
for the existing catalogue, move such lines out of a description into the
fields (``tidy``). The composed description writes the fields back under the
paragraph, so a line left in the prose would say everything twice.

A line is taken only when its value reads as data. "Notas de Topo: Bergamota,
Menta" goes; "Notas de Topo: A abertura e marcada pela bergamota" is a sentence
and stays, and so does a family line with a word that names no family
("Familia: Fougere Marinho"). Everything else in the description is left
exactly as written.

Pure functions on strings: the migration runs them against historical models,
so nothing here may touch the ORM. Families come in as ``(id, name, tag)``.
"""
import re
import unicodedata
from dataclasses import dataclass, field

from ..management.commands.backfill_perfume_attributes import KNOWN_HOUSES
from .note_parser import PROSE_MARKERS, TAIL_CLAUSE

LAYER_WORDS = {
    'top': ['topo', 'saída', 'saida', 'cabeça', 'cabeca', 'abertura', 'top'],
    'heart': ['coração', 'coracao', 'corpo', 'centro', 'meio', 'heart'],
    'base': ['base', 'fundo'],
}
NOTE_FIELDS = {'top': 'notes_top', 'heart': 'notes_heart', 'base': 'notes_base'}
_WORD = {word: layer for layer, words in LAYER_WORDS.items() for word in words}
_WORDS = '|'.join(sorted((re.escape(w) for w in _WORD), key=len, reverse=True))
_LEAD = r'^[^\w]*\s*'                      # bullets and emoji before a label
_SEP = r'(?:\s*\([^)]*\))?\s*(?:s[ãa]o|[ée])?\s*[:：\-–—]\s*'

LAYER_LINE = re.compile(
    _LEAD + r'(?:notas?\s+(?:de|da|do)\s+)?(?P<word>' + _WORDS + r')(?:\s+notes?)?' + _SEP + r'(?P<value>.*)$',
    re.IGNORECASE)
# "Notas de topo salvia, alfazema" - no separator, so the word "notas" is required.
BARE_LAYER_LINE = re.compile(
    _LEAD + r'notas?\s+(?:de|da|do)\s+(?P<word>' + _WORDS + r')\s+(?:s[ãa]o\s+|[ée]\s+)?(?P<value>\S.*)$',
    re.IGNORECASE)
FAMILY_LINE = re.compile(_LEAD + r'fam[ií]lia(?:\s+olfac?tiva)?' + _SEP + r'(?P<value>.+)$', re.IGNORECASE)
FAMILY_PHRASE = re.compile(r'fam[ií]lia\s+(?:olfac?tiva\s+)?(?P<value>[^\n.,;:()]{3,60})', re.IGNORECASE)
INSPIRED_LINE = re.compile(
    _LEAD + r'inspirad[oa]s?\s+(?:em|no|na|nos|nas|pelo|pela)\b\s*[:：]?\s*(?P<value>.+)$', re.IGNORECASE)
GENDER_LINE = re.compile(
    _LEAD + r'(?:g[éeê]nero|tipo\s+de\s+fragr[âa]ncia|sexo)' + _SEP + r'(?P<value>[^\n|]+)$', re.IGNORECASE)
# A heading over the notes, dropped once the lines beneath it have moved.
HEADING_LINE = re.compile(
    _LEAD + r'(?:notas?\s+(?:olfac?tivas?|da\s+fragr[âa]ncia|do\s+perfume)|pir[âa]mide\s+olfac?tiva(?:\s+d[eoa]\s+.{1,40})?)'
    r'\s*[:：]?[^\w\n]*$', re.IGNORECASE)

# A note value that opens like a sentence ("Uma explosao de ...").
LEADING_PROSE = re.compile(r'^(?:um|uma|uns|umas|o|a|os|as|com|no|na|nos|nas|em|para|este|esta)\b', re.IGNORECASE)
SENTENCE_BREAK = re.compile(r'[.!?]\s+\S')
# "Bergamota - sofisticacao e frescor": a spaced dash starts a comment.
SPACED_DASH = re.compile(r'\s[–—-]\s')

STOP_WORDS = {'de', 'da', 'do', 'das', 'dos', 'e', 'com', 'um', 'uma', 'toque', 'notas', 'nota'}
# Portuguese and English words for the shop's families, by the English word
# that names each family row ("Woody", "Sweet / Gourmand" ...).
FAMILY_STEMS = [
    ('amadeirad', 'woody'), ('madeira', 'woody'), ('woody', 'woody'),
    ('flor', 'floral'),
    ('frut', 'fruity'), ('fruity', 'fruity'),
    ('doce', 'sweet'), ('gourmand', 'sweet'), ('sweet', 'sweet'),
    ('fresc', 'fresh'), ('citric', 'fresh'), ('citrus', 'fresh'), ('fresh', 'fresh'), ('aquatic', 'fresh'),
    ('especi', 'spicy'), ('spicy', 'spicy'),
    ('oriental', 'oriental'), ('orientai', 'oriental'), ('ambar', 'oriental'), ('amber', 'oriental'),
    ('almisc', 'musk'), ('musk', 'musk'),
    ('couro', 'leather'), ('tabaco', 'leather'), ('leather', 'leather'), ('tobacco', 'leather'),
    ('oud', 'oud'),
]
GENDER_WORDS = [
    ('unisex', r'unisse?x|unisex|ambos|homens?\s+e\s+mulher(?:es)?|mulher(?:es)?\s+e\s+homens?|todos'),
    ('women', r'feminin[oa]s?|mulher(?:es)?|women|woman|femme'),
    ('men', r'masculin[oa]s?|homens?|\bmen\b|\bman\b|homme'),
]

# In running prose "todos" and "ambos" mean anything; only these say unisex.
UNISEX_PROSE = r'\bunisse?x\w*|homens?\s+e\s+mulher(?:es)?|mulher(?:es)?\s+e\s+homens?|para\s+ambos'
# "Inspirado na natureza" is a metaphor; a house name or a colon makes it a reference.
HOUSES = re.compile('|'.join(sorted((re.escape(h) for h in KNOWN_HOUSES), key=len, reverse=True)), re.IGNORECASE)

NOTE_MAX, INSPIRATION_MAX, GENDER_LINE_MAX = 200, 120, 25


@dataclass
class ScentText:
    prose: str = ''
    notes: dict = field(default_factory=dict)        # layer -> value
    families: list = field(default_factory=list)     # family ids, in the text's order
    inspiration: str = ''
    gender: str = ''


def _plain(text):
    text = unicodedata.normalize('NFKD', text or '')
    return ''.join(c for c in text if not unicodedata.combining(c)).lower()


def _squash(value):
    """One line of text, without emoji and the punctuation around it."""
    value = ''.join(c for c in (value or '')
                    if unicodedata.category(c) != 'So' and c not in '️‍')
    return re.sub(r'\s+', ' ', value).strip().strip(' .;:,-–—').strip()


def _is_list(value):
    """Notes are a short list of ingredients, not a sentence about them."""
    if not (2 <= len(value) <= NOTE_MAX) or ':' in value:
        return False
    if PROSE_MARKERS.search(value) or TAIL_CLAUSE.search(value) or SENTENCE_BREAK.search(value):
        return False
    if LEADING_PROSE.match(value) or SPACED_DASH.search(value):
        return False
    items = re.split(r'[,;/]|\s+e\s+', value)
    return all(len(item.strip()) <= 40 for item in items)


def _family_index(families):
    """Every word of a family's name and tag -> its id ("amadeirado", "woody")."""
    index = {}
    for pk, name, tag in families:
        for word in re.findall(r'[a-z]+', _plain(f'{name} {tag or ""}')):
            index.setdefault(word, pk)
    return index


def _family_of(word, index):
    if word in index:
        return index[word]
    for stem, key in FAMILY_STEMS:
        if word.startswith(stem) and key in index:
            return index[key]
    return None


def family_ids(value, families, *, leading=False):
    """The family ids a phrase names, or None if a word names none.

    ``leading``: read only the words up to the first that is not a family -
    for a phrase inside a sentence ("da familia oriental frutada que ...")."""
    index = _family_index(families)
    found = []
    for word in re.findall(r'[a-z]+', _plain(value)):
        if word in STOP_WORDS:
            continue
        pk = _family_of(word, index)
        if pk is None:
            if leading:
                break
            return None
        if pk not in found:
            found.append(pk)
    return found or None


def gender_of(value):
    text = _plain(value)
    for code, pattern in GENDER_WORDS:
        if re.search(pattern, text):
            return code
    return ''


def _gender_in_prose(text):
    plain = _plain(text)
    if re.search(UNISEX_PROSE, plain):
        return 'unisex'
    women = re.search(r'\b(?:feminin[oa]s?|para\s+mulher(?:es)?|for\s+women)\b', plain)
    men = re.search(r'\b(?:masculin[oa]s?|para\s+homens?|for\s+men)\b', plain)
    if women and men:
        return 'unisex'
    return 'women' if women else 'men' if men else ''


LABELS = (LAYER_LINE, BARE_LAYER_LINE, FAMILY_LINE, INSPIRED_LINE, GENDER_LINE, HEADING_LINE)


def _runs_on(lines, index):
    """The next line, with no blank between, is ordinary text."""
    following = lines[index + 1].strip() if index + 1 < len(lines) else ''
    return bool(following) and not any(label.match(following) for label in LABELS)


def _value_below(lines, start):
    """A label with nothing after it: its value is on the lines beneath."""
    taken = []
    for index in range(start + 1, min(start + 5, len(lines))):
        text = lines[index].strip()
        if not text or LAYER_LINE.match(text) or HEADING_LINE.match(text):
            break
        taken.append(index)
    return ', '.join(lines[i].strip() for i in taken), taken


def read_scent_text(text, families, *, gender=False, phrases=False):
    """What a description states as labelled lines, and the prose without them.

    ``gender`` and ``phrases`` are for a pasted paragraph: read the gender, and
    a family named inside a sentence. Neither removes any prose."""
    lines = (text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    result = ScentText()
    removed = set()
    for index, line in enumerate(lines):
        if index in removed:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        match = LAYER_LINE.match(stripped) or BARE_LAYER_LINE.match(stripped)
        if match:
            value, below = match.group('value'), []
            if not _squash(value) and match.re is LAYER_LINE:
                value, below = _value_below(lines, index)
            value = _squash(value)
            # Without a separator only a list with commas is safe to read.
            bare_ok = match.re is LAYER_LINE or ',' in value
            # "Notas de Saida - Explosao Citrica" with its own lines right under
            # it is a heading over them, not a value.
            heading = not below and _runs_on(lines, index)
            if bare_ok and not heading and _is_list(value):
                result.notes.setdefault(_WORD[match.group('word').lower()], value)
                removed.update([index, *below])
            continue
        match = FAMILY_LINE.match(stripped)
        if match:
            ids = family_ids(match.group('value'), families)
            if ids:
                for pk in ids:
                    if pk not in result.families:
                        result.families.append(pk)
                removed.add(index)
            continue
        match = INSPIRED_LINE.match(stripped)
        if match:
            value = _squash(match.group('value'))
            stated = ':' in stripped[:match.start('value')] or HOUSES.search(value)
            if (value and stated and len(value) <= INSPIRATION_MAX and not PROSE_MARKERS.search(value)
                    and not SENTENCE_BREAK.search(value) and not TAIL_CLAUSE.search(value)):
                result.inspiration = result.inspiration or value
                removed.add(index)
            continue
        if gender:
            match = GENDER_LINE.match(stripped)
            if match and gender_of(match.group('value')):
                result.gender = result.gender or gender_of(match.group('value'))
                # "Genero: Masculino - Familia amadeirada" says more: keep it.
                if len(_squash(match.group('value'))) <= GENDER_LINE_MAX:
                    removed.add(index)

    # A heading whose lines have all moved goes with them.
    for index, line in enumerate(lines):
        if index in removed or not HEADING_LINE.match(line.strip()):
            continue
        following = next((i for i in range(index + 1, len(lines)) if lines[i].strip()), None)
        if following is None or following in removed:
            removed.add(index)

    kept = [line for index, line in enumerate(lines) if index not in removed]
    prose = re.sub(r'\n[ \t]*(?:\n[ \t]*)+\n', '\n\n', '\n'.join(kept)).strip()
    result.prose = prose if removed else (text or '').strip()

    if phrases and not result.families:
        for match in FAMILY_PHRASE.finditer(result.prose):
            ids = family_ids(match.group('value'), families, leading=True)
            if ids:
                result.families = ids
                break
    if gender and not result.gender:
        result.gender = _gender_in_prose(result.prose)
    return result


def tidy(data, families):
    """The saved-description clean-up: ``data`` holds name, model, description,
    notes_top/heart/base, inspired_by_text and family_ids; returns it tidied.

    A name equal to the model goes. A recognised line leaves the description
    and fills its field when the field is empty; a field already filled wins.
    """
    out = dict(data)
    name, model = (data.get('name') or '').strip(), (data.get('model') or '').strip()
    if name and name.lower() == model.lower():
        out['name'] = ''
    description = data.get('description') or ''
    read = read_scent_text(description, families)
    if read.prose != description.strip():
        out['description'] = read.prose
    for layer, value in read.notes.items():
        if not (data.get(NOTE_FIELDS[layer]) or '').strip():
            out[NOTE_FIELDS[layer]] = value
    if read.inspiration and not (data.get('inspired_by_text') or '').strip():
        out['inspired_by_text'] = read.inspiration
    if read.families and not data.get('family_ids'):
        out['family_ids'] = list(read.families)
    return out
