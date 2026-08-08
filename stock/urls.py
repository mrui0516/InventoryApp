# stock/urls.py
from django.urls import path
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.conf.urls.static import static

from . import views


urlpatterns = [
    path('login/', auth_views.LoginView.as_view(template_name='stock/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),

    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('sales-trend/', views.yearly_sales_view, name='sales_trend'),
    path('inbound/', views.inbound_view, name='inbound'),
    path('outbound/', views.outbound_view, name='outbound'),
    path('products/', views.product_list_view, name='product_list'),
    path('products/export-excel/', views.export_product_list_excel, name='export_product_list_excel'),
    path('products/export-shopify-csv/', views.export_shopify_inventory_csv, name='export_shopify_inventory_csv'),
    path('check_barcode/', views.check_barcode, name='check_barcode'),
    path('add-product/', views.add_product_view, name='add_product'),
    path('products/<int:pk>/', views.product_detail_view, name='product_detail'),
    path('sales-history/', views.product_sales_history_view, name='product_sales_history'),
    path('backup/download/', views.download_db_backup, name='download_db_backup'),
    path('products/<int:pk>/sync-shopify/', views.sync_product_to_shopify, name='sync_product_to_shopify'),
    path('products/sync-shopify-all/', views.sync_all_perfumes_to_shopify, name='sync_all_perfumes_to_shopify'),
    path('products/<int:pk>/edit/', views.edit_product_view, name='edit_product'),
    path('products/<int:pk>/delete/', views.delete_product_view, name='delete_product'),

    path('export-sales-purchases-pdf/', views.export_sales_purchases_pdf, name='export_sales_purchases_pdf'),
    path('sales-records/', views.record_view, name='sales_records'),
    path('today/', views.daily_summary_view, name='daily_summary'),
    path('stores/switch/', views.set_active_store, name='set_active_store'),
    path('stores/', views.store_list_view, name='store_list'),
    path('stores/new/', views.store_create_view, name='store_create'),
    path('stores/<int:store_id>/edit/', views.store_edit_view, name='store_edit'),
    path('stores/<int:store_id>/delete/', views.store_delete_view, name='store_delete'),
    path('product/image/delete/<int:image_id>/', views.delete_product_image, name='delete_product_image'),

    path('check_customer/', views.check_customer, name='check_customer'),
    path('api/customers_autocomplete/', views.customers_autocomplete, name='customers_autocomplete'),
    path('add_customer_ajax/', views.add_customer, name='add_customer_ajax'),
    path('customers/', views.customer_search_view, name='customer_search'),
    path('customers/<int:customer_id>/', views.customer_detail_view, name='customer_detail'),
    path('customers/<int:customer_id>/edit/', views.edit_customer_view, name='edit_customer'),
    path('customers/delete/<int:pk>/', views.delete_customer, name='delete_customer'),

    path('suppliers/', views.supplier_list_view, name='supplier_list'),
    path('suppliers/new/', views.supplier_create_view, name='supplier_create'),
    path('suppliers/<int:supplier_id>/', views.supplier_detail_view, name='supplier_detail'),
    path('suppliers/<int:supplier_id>/edit/', views.supplier_edit_view, name='supplier_edit'),
    path('suppliers/<int:supplier_id>/delete/', views.supplier_delete_view, name='supplier_delete'),
    path('inbound/<int:order_id>/receive/', views.inbound_receive_view, name='inbound_receive'),
    path('api/suppliers_autocomplete/', views.suppliers_autocomplete, name='suppliers_autocomplete'),
    path('inbound-records/<int:order_id>/edit/', views.inbound_order_edit_view, name='inbound_order_edit'),
    path('direct-purchases/<int:purchase_id>/edit/', views.direct_purchase_edit_view, name='direct_purchase_edit'),
    path('api/products_autocomplete/', views.products_autocomplete, name='products_autocomplete'),

    path('catalog/', views.catalog_view, name='catalog'),
    path('catalog/export-excel/', views.export_catalog_excel, name='export_catalog_excel'),

    path('team/', views.employee_list_view, name='employee_list'),
    path('team/new/', views.employee_create_view, name='employee_create'),
    path('team/<int:user_id>/edit/', views.employee_edit_view, name='employee_edit'),
    path('team/<int:user_id>/toggle-active/', views.employee_toggle_active_view, name='employee_toggle_active'),
    path('team/<int:user_id>/delete/', views.employee_delete_view, name='employee_delete'),
    path('attendance/', views.attendance_view, name='attendance'),

    path('sale-orders/manage/', views.sale_order_correction_center_view, name='sale_order_correction_center'),
    path('sale-orders/manage/new/', views.sale_order_correction_create_view, name='sale_order_correction_create'),
    path('sale-orders/manage/<int:order_id>/edit/', views.sale_order_correction_edit_view, name='sale_order_correction_edit'),
    path('print-profile/', views.print_profile_edit_view, name='print_profile_edit'),
    path('sale-orders/<int:order_id>/', views.sale_order_detail_view, name='sale_order_detail'),

    path('ar/new/', views.ar_new_view, name='ar_new'),
    path('ar/', views.ar_list_view, name='ar_list'),
    path('ar/<int:invoice_id>/', views.ar_detail_view, name='ar_detail'),
    path('ar/<int:invoice_id>/add-payment/', views.ar_add_payment_view, name='ar_add_payment'),
    path('ar/<int:invoice_id>/add-items/', views.ar_add_items_view, name='ar_add_items'),

    path('api/adjust_purchase_stock/', views.api_adjust_purchase_stock, name='api_adjust_purchase_stock'),
    path('api/adjust_total_stock/', views.api_adjust_total_stock, name='api_adjust_total_stock'),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
