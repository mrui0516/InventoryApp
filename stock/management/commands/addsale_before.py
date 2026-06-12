from datetime import datetime
from django.utils import timezone
from django.db import transaction
from stock.models import Sale
from stock.views import apply_fifo  # 你项目里已有的 FIFO 扣减函数


##第一步 今天售出前几天差的销售，改成之前日期
# 1) 目标时间：昨天 15:30（按需改）
target_dt = timezone.make_aware(datetime(2025, 8, 9, 21, 00))

# 2) 选择需要回填的销售行：
# 方案一：按订单ID
order_ids = [284]  # ← 改成你刚建的订单号们
qs = Sale.objects.filter(order_id__in=order_ids)


print("Will backdate rows:", qs.count())

with transaction.atomic():
    # 2.1 修改日期
    for s in qs:
        s.date = target_dt
        s.save(update_fields=['date'])


print("OK: backdated applied.")


##第二步
##让order和第一个sale时间保持一致
from django.db.models import Min
from stock.models import SaleOrder

qs = (SaleOrder.objects
      .annotate(first_item_time=Min('items__date'))
      .filter(first_item_time__isnull=False))

updated = 0
for o in qs:
    if o.created_at != o.first_item_time:
        o.created_at = o.first_item_time   # 或者改成 Max('items__date')
        o.save(update_fields=['created_at'])
        updated += 1

print(f"Fixed {updated} orders.")
