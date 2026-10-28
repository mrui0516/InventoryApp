from django.contrib import admin
from django.utils import timezone
from .models import RestockSubscription,RestockEmail,MessagingSlot


@admin.register(RestockSubscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('product_title','variant_title','sku','email','status','confirmed_at','notified_at')
    list_filter = ('status',)
    search_fields = ('product_title','sku','email')
    readonly_fields = tuple(field.name for field in RestockSubscription._meta.fields)
    change_list_template = 'restock_alerts/admin_list.html'

    def has_add_permission(self,request):
        return False


@admin.register(RestockEmail)
class EmailAdmin(admin.ModelAdmin):
    list_display = ('subscription','provider','kind','status','attempts','sent_at','last_error')
    list_filter = ('provider','status','kind')
    readonly_fields = tuple(field.name for field in RestockEmail._meta.fields)
    actions = ['retry_failed']

    def has_add_permission(self,request):
        return False

    @admin.action(description='Retry failed emails')
    def retry_failed(self,request,queryset):
        count=queryset.filter(status=RestockEmail.FAILED,provider='smtp').update(status=RestockEmail.PENDING,attempts=0,available_at=timezone.now(),lease_until=None)
        self.message_user(request,f'{count} SMTP email(s) queued for retry. Messaging failures require inspecting the Flow run; they are never blindly resent.')

    def has_delete_permission(self,request,obj=None):
        return False


@admin.register(MessagingSlot)
class MessagingSlotAdmin(admin.ModelAdmin):
    list_display = ('email','state','job','started_at','checked_at')
    list_filter = ('state',)
    readonly_fields = tuple(field.name for field in MessagingSlot._meta.fields)

    def has_add_permission(self,request):
        return False

    def has_delete_permission(self,request,obj=None):
        return False
