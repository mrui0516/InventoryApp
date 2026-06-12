from django.contrib import admin, messages
from django.core.management import call_command
from django.utils.html import format_html, mark_safe
from django.urls import reverse
from django.db.models import Sum
from decimal import Decimal
from .models import (
    Product, Purchase, Sale, SaleOrder, Brand, ProductSeries,
    Category, Supplier, Customer, ProductImage, DailySalesSummary, InboundOrder,
    ARInvoice, ARItem, ARPayment, AttendanceRecord, PrintProfile
)

# 统一产品显示
def format_product_display(obj):
    return f"{obj.display_name} ({obj.barcode})"


# ---------- Product ----------
class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    inlines = [ProductImageInline]
    list_display = ('formatted_name', 'barcode', 'brand', 'category', 'stock_display', 'get_fifo_price', 'default_price', 'wholesale_price')
    search_fields = ('name', 'brand', 'model', 'spec', 'color', 'barcode')
    list_filter = ('brand', 'category')
    list_editable = ('default_price', 'wholesale_price')
    list_per_page = 50
    readonly_fields = ('created_at', 'fifo_cost_display', 'stock_history_display')
    fieldsets = (
        ('Product Info', {
            'fields': (
                'category', 'brand_master', 'series_master', 'name', 'spec', 'color',
                'barcode', 'brand', 'model', 'description', 'default_price',
                'wholesale_price', 'created_at'
            )
        }),
        ('Inventory & Cost', {
            'fields': ('fifo_cost_display', 'stock_history_display')
        }),
    )

    @admin.display(description='Product')
    def formatted_name(self, obj):
        return format_product_display(obj)

    @admin.display(description='Total Stock')
    def stock_display(self, obj):
        total = obj.total_stock()
        if total > 5:
            color = '#28a745'
        elif total > 0:
            color = '#ffc107'
        else:
            color = '#dc3545'
        return format_html(
            '<span style="color:{}; font-weight:700;">{}</span>',
            color, total
        )

    @admin.display(description='FIFO Price')
    def get_fifo_price(self, obj):
        return f"€{obj.current_fifo_cost_price():.2f}"

    @admin.display(description='Current FIFO Cost')
    def fifo_cost_display(self, obj):
        fifo = obj.current_fifo_cost_price()
        return f"€{fifo:.2f}"

    @admin.display(description='Stock History (Click to Edit)')
    def stock_history_display(self, obj):
        """
        显示库存批次历史，点击任何行可跳转到对应 Purchase 记录编辑页面
        """
        purchases = obj.purchase_set.filter(remaining__gt=0).order_by('-date')[:10]
        if not purchases.exists():
            return "No on-hand stock records"
        
        html = '''
        <table style="width:100%; border-collapse:collapse; cursor:pointer;">
        <tr style="background:#f5f5f5; border-bottom:2px solid #ddd;">
            <th style="text-align:left; padding:10px; font-weight:700;">Date</th>
            <th style="text-align:right; padding:10px; font-weight:700;">Qty</th>
            <th style="text-align:right; padding:10px; font-weight:700;">Remain</th>
            <th style="text-align:right; padding:10px; font-weight:700;">Cost</th>
        </tr>
        '''
        
        for p in purchases:
            edit_url = reverse('admin:stock_purchase_change', args=[p.id])
            html += f'''
            <tr style="border-bottom:1px solid #eee; transition:background 0.2s;" 
                onmouseover="this.style.background='#f0f0ff'" 
                onmouseout="this.style.background=''" 
                onclick="window.location.href='{edit_url}'; return false;">
                <td style="padding:10px;"><a href="{edit_url}" style="color:inherit; text-decoration:none;">{p.date.strftime("%Y-%m-%d %H:%M")}</a></td>
                <td style="text-align:right; padding:10px;"><a href="{edit_url}" style="color:inherit; text-decoration:none;">{p.quantity}</a></td>
                <td style="text-align:right; padding:10px; font-weight:700;"><a href="{edit_url}" style="color:#007bff; text-decoration:none;">{p.remaining}</a></td>
                <td style="text-align:right; padding:10px;"><a href="{edit_url}" style="color:inherit; text-decoration:none;">€{p.cost_price:.2f}</a></td>
            </tr>
            '''
        
        html += '</table>'
        return format_html(html)


# ---------- Purchase ----------
@admin.register(InboundOrder)
class InboundOrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'supplier', 'invoice_no', 'invoice_date', 'total_amount', 'created_at')
    list_filter = ('supplier', 'invoice_date', 'created_at')
    search_fields = ('invoice_no', 'supplier__name')

@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ('id', 'product_link', 'quantity', 'remaining_display', 'cost_price', 'date', 'supplier')
    list_filter = ('supplier', 'date', 'product')
    search_fields = ('product__name', 'product__barcode', 'inbound_order__invoice_no')
    list_select_related = ('product', 'supplier')
    list_per_page = 50
    
    # 编辑页面字段
    readonly_fields = ('product', 'quantity', 'cost_price', 'date', 'inbound_order', 'supplier')
    fields = ('product', 'inbound_order', 'supplier', 'quantity', 'cost_price', 'remaining', 'date')
    
    # 仅 remaining 字段可编辑
    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ('product', 'quantity', 'cost_price', 'date', 'inbound_order', 'supplier')
        return ()

    @admin.display(description='Product')
    def product_link(self, obj):
        url = reverse('admin:stock_product_change', args=[obj.product.id])
        return format_html(
            '<a href="{}">{}</a>',
            url,
            format_product_display(obj.product)
        )

    @admin.display(description='Remaining Stock')
    def remaining_display(self, obj):
        if obj.remaining > 0:
            return format_html('<span style="color:#28a745; font-weight:700;">{}</span>', obj.remaining)
        else:
            return format_html('<span style="color:#dc3545; font-weight:700;">0</span>')


# ---------- Sale（销售行） ----------
@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ('order_link', 'get_product_display', 'customer', 'quantity', 'unit_price', 'line_total', 'payment_method', 'date')
    list_filter = ('date', 'payment_method')
    date_hierarchy = 'date'
    search_fields = ('product__name', 'product__brand', 'product__model', 'product__barcode', 'customer__name', 'order__id')
    list_select_related = ('product', 'customer', 'order')
    list_per_page = 50

    @admin.display(description='Order')
    def order_link(self, obj):
        if obj.order_id:
            return f"#{obj.order_id}"
        return "-"

    @admin.display(description='Product')
    def get_product_display(self, obj):
        return format_product_display(obj.product)

    @admin.display(description='Line Total')
    def line_total(self, obj):
        return obj.unit_price * obj.quantity


# ---------- SaleOrder（订单头） ----------
class SaleInline(admin.TabularInline):
    model = Sale
    extra = 0
    fields = ('product', 'quantity', 'unit_price', 'payment_method', 'date')
    readonly_fields = ('date',)
    autocomplete_fields = ('product',)
    show_change_link = True

@admin.register(SaleOrder)
class SaleOrderAdmin(admin.ModelAdmin):
    inlines = [SaleInline]
    list_display = ('id', 'customer', 'created_at', 'items_count', 'order_total')
    search_fields = ('id', 'customer__name', 'customer__nif')
    list_select_related = ('customer',)
    ordering = ('-created_at',)
    list_per_page = 50

    @admin.display(description='Items')
    def items_count(self, obj):
        return obj.items.count()

    @admin.display(description='Total')
    def order_total(self, obj):
        return obj.total_amount


# ---------- Others ----------
admin.site.register(Category)
admin.site.register(Brand)
admin.site.register(ProductSeries)
admin.site.register(Supplier)
admin.site.register(Customer)

@admin.register(DailySalesSummary)
class DailySalesSummaryAdmin(admin.ModelAdmin):
    list_display = ('date', 'total_sales', 'total_profit', 'total_items_sold')
    ordering = ('-date',)
    readonly_fields = ('date', 'total_sales', 'total_profit', 'total_items_sold')
    actions = ["rebuild_selected_summaries"]

    @admin.action(description="🔄 Rebuild selected Daily Summaries (FIFO)")
    def rebuild_selected_summaries(self, request, queryset):
        try:
            for summary in queryset:
                call_command("rebuild_dailysummary", date=summary.date.strftime("%Y-%m-%d"))
            self.message_user(request, f"✅ Rebuilt {queryset.count()} summaries.", level=messages.SUCCESS)
        except Exception as e:
            self.message_user(request, f"❌ Error: {e}", level=messages.ERROR)


@admin.register(ARInvoice)
class ARInvoiceAdmin(admin.ModelAdmin):
    list_display = ('id','customer','date','due_date','status','total_amount','amount_paid','created_at')
    list_filter = ('status','date','due_date','created_at')
    search_fields = ('customer__name','customer__nif')

@admin.register(ARItem)
class ARItemAdmin(admin.ModelAdmin):
    list_display = ('invoice','product_name','quantity','unit_price')

@admin.register(ARPayment)
class ARPaymentAdmin(admin.ModelAdmin):
    list_display = ('invoice', 'created_at', 'amount', 'method')
    list_filter = ('method', 'created_at')
    date_hierarchy = 'created_at'


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ('user', 'clock_in_at', 'clock_out_at', 'note')
    list_filter = ('clock_in_at', 'clock_out_at')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'note')
    date_hierarchy = 'clock_in_at'


@admin.register(PrintProfile)
class PrintProfileAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone', 'email', 'updated_at')

