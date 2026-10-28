from django.apps import AppConfig


class StockConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'stock'

    def ready(self):
        from . import signals  # noqa: F401
        from django.contrib.auth.signals import user_login_failed
        from .system_log import record_failed_login
        user_login_failed.connect(record_failed_login, dispatch_uid='stock.failed_login')
