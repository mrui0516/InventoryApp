"""One row per form submission that created records (Sell, Inbound).

The form carries a random token from the moment the page is shown; the view
inserts the token in the same transaction as the records it creates. The same
form posted again - a refresh, a browser that resends after being reopened,
a double tap - hits the unique key and is not recorded a second time. The old
guard, an 8-second in-memory cache entry, let anything later than that through,
and on PythonAnywhere was not even shared between server processes.
"""
from django.conf import settings
from django.db import models


class FormSubmission(models.Model):
    token = models.CharField(max_length=64, unique=True)
    path = models.CharField(max_length=200)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                             null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f"{self.path} {self.token[:8]}"
