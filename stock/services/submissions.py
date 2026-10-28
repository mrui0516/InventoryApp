"""Record a form submission once, however often it is posted.

Call :func:`claim` inside the ``transaction.atomic()`` block that creates the
records. If the token was claimed before, it raises :class:`AlreadySubmitted`
and the transaction rolls back; if the block fails for any other reason, the
claim rolls back with it and the person can simply try again.
"""
import re

from django.db import IntegrityError, transaction

TOKEN_RE = re.compile(r'^[0-9a-f]{32}$')


class AlreadySubmitted(Exception):
    pass


def token_of(request):
    token = (request.POST.get('submit_token') or '').strip().lower()
    return token if TOKEN_RE.match(token) else ''


def claim(request):
    """Claim this POST's token. A form without one (a page cached before this
    existed) is let through - the caller's own checks still apply."""
    from ..models import FormSubmission
    token = token_of(request)
    if not token:
        return
    try:
        with transaction.atomic():          # a savepoint: the outer block survives
            FormSubmission.objects.create(
                token=token, path=request.path[:200],
                user=request.user if request.user.is_authenticated else None)
    except IntegrityError:
        raise AlreadySubmitted


def already_claimed(request):
    from ..models import FormSubmission
    token = token_of(request)
    return bool(token) and FormSubmission.objects.filter(token=token).exists()
