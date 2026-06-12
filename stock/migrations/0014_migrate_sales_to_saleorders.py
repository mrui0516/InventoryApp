# stock/migrations/00xx_migrate_sales_to_saleorders.py
from django.db import migrations

def forwards(apps, schema_editor):
    Sale = apps.get_model('stock', 'Sale')
    SaleOrder = apps.get_model('stock', 'SaleOrder')
    db_alias = schema_editor.connection.alias

    # 一条旧 Sale 建一条 SaleOrder，并把 sale.order 指向它
    for sale in Sale.objects.using(db_alias).filter(order__isnull=True):
        order = SaleOrder.objects.using(db_alias).create(
            customer=sale.customer,  # 能保留就保留
            # created_at 用 auto_now_add，无法指定；如果想复用 sale.date，可在模型里去掉 auto_now_add 再回写
        )
        sale.order = order
        # 字段已 Rename 成 unit_price，数据不丢
        sale.save(update_fields=['order'])

def backwards(apps, schema_editor):
    # 反向迁移不做任何事
    pass

class Migration(migrations.Migration):
    dependencies = [
        ('stock', '0013_saleorder_rename_sale_price_sale_unit_price_and_more'),  # 把这里换成上一步生成的迁移名
    ]
    operations = [
        migrations.RunPython(forwards, backwards),
    ]
