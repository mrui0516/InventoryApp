from django.urls import path
from . import views

urlpatterns = [
    path('subscribe/',views.subscribe,name='restock_subscribe'),
    path('status/',views.status,name='restock_status'),
    path('confirm/<uuid:token>/',views.confirm,name='restock_confirm'),
    path('unsubscribe/<uuid:token>/',views.unsubscribe,name='restock_unsubscribe'),
    path('demand/',views.demand,name='restock_demand'),
]
