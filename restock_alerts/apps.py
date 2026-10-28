from django.apps import AppConfig


class RestockAlertsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'restock_alerts'
    verbose_name = '到货邮件与产品需求'
