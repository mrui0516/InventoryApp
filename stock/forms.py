# stock/forms.py
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Q
from django.forms import formset_factory, inlineformset_factory
from django.utils import timezone

from .models import (
    ARInvoice,
    ARItem,
    ARPayment,
    Brand,
    Customer,
    InboundOrder,
    PrintProfile,
    Product,
    ProductSeries,
    Purchase,
    Sale,
    SaleOrder,
    Supplier,
)


class BarcodeForm(forms.Form):
    barcode = forms.CharField(label="Scan or Enter Barcode", max_length=13)


class ProductForm(forms.ModelForm):
    brand_master = forms.ModelChoiceField(
        queryset=Brand.objects.none(),
        required=False,
        label="Brand",
        empty_label="Select brand",
    )
    new_brand_name = forms.CharField(required=False, label="Or add brand", max_length=80)
    series_master = forms.ModelChoiceField(
        queryset=ProductSeries.objects.none(),
        required=False,
        label="Series / Model",
        empty_label="Select series / model",
    )
    new_series_name = forms.CharField(required=False, label="Or add series / model", max_length=100)

    class Meta:
        model = Product
        fields = [
            'barcode',
            'category',
            'brand_master',
            'series_master',
            'name',
            'spec',
            'color',
            'default_price',
            'wholesale_price',
            'description',
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
            'default_price': forms.NumberInput(attrs={
                'step': '0.01',
                'min': '0',
                'max': '99999',
                'class': 'form-control',
            }),
            'wholesale_price': forms.NumberInput(attrs={
                'step': '0.01',
                'min': '0',
                'max': '99999',
                'class': 'form-control',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        def parse_pk(value):
            if value in (None, ''):
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        selected_category_id = parse_pk(self.data.get('category'))
        selected_brand_id = parse_pk(self.data.get('brand_master'))
        if not selected_category_id and getattr(self.instance, 'category_id', None):
            selected_category_id = self.instance.category_id

        brand_qs = Brand.objects.order_by('name')
        if selected_category_id:
            brand_filter = Q(categories__id=selected_category_id)
            if selected_brand_id:
                brand_filter |= Q(id=selected_brand_id)
            if getattr(self.instance, 'brand_master_id', None):
                brand_filter |= Q(id=self.instance.brand_master_id)
            brand_qs = brand_qs.filter(brand_filter).distinct()
        self.fields['brand_master'].queryset = brand_qs

        selected_brand = None
        if selected_brand_id:
            try:
                selected_brand = Brand.objects.get(pk=selected_brand_id)
            except (Brand.DoesNotExist, TypeError, ValueError):
                selected_brand = None
        elif getattr(self.instance, 'brand_master_id', None):
            selected_brand = self.instance.brand_master

        series_qs = ProductSeries.objects.order_by('name')
        if selected_brand:
            series_qs = series_qs.filter(brand=selected_brand)
        self.fields['series_master'].queryset = series_qs

        if getattr(self.instance, 'brand_master_id', None) and not self.initial.get('brand_master'):
            self.initial['brand_master'] = self.instance.brand_master_id
        if getattr(self.instance, 'series_master_id', None) and not self.initial.get('series_master'):
            self.initial['series_master'] = self.instance.series_master_id

        self.fields['default_price'].label = 'Retail Price'
        self.fields['wholesale_price'].label = 'Wholesale Price'
        self.fields['name'].label = 'Product Name'
        self.fields['spec'].label = 'Specification'
        self.fields['color'].label = 'Color'

    def clean(self):
        cleaned = super().clean()
        brand = cleaned.get('brand_master')
        new_brand_name = (cleaned.get('new_brand_name') or '').strip()
        series = cleaned.get('series_master')
        new_series_name = (cleaned.get('new_series_name') or '').strip()

        if not brand and not new_brand_name:
            self.add_error('brand_master', 'Select an existing brand or add a new brand.')

        if new_brand_name:
            brand, _created = Brand.objects.get_or_create(name=new_brand_name)

        if series and brand and series.brand_id != brand.id:
            self.add_error('series_master', 'Selected series / model does not belong to the chosen brand.')

        if new_series_name:
            if not brand:
                self.add_error('series_master', 'Choose a brand before adding a new series / model.')
            else:
                series, _created = ProductSeries.objects.get_or_create(
                    brand=brand,
                    name=new_series_name,
                )

        cleaned['resolved_brand_master'] = brand
        cleaned['resolved_series_master'] = series
        return cleaned

    def save(self, commit=True):
        product = super().save(commit=False)
        product.brand_master = self.cleaned_data.get('resolved_brand_master')
        product.series_master = self.cleaned_data.get('resolved_series_master')

        if product.brand_master:
            product.brand = product.brand_master.name
        if product.series_master:
            product.model = product.series_master.name
        elif not product.model:
            product.model = None

        if commit:
            product.save()
            if product.brand_master and product.category_id:
                product.brand_master.categories.add(product.category)
            self.save_m2m()
        return product


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ['name', 'phone', 'country', 'address', 'product_types']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Supplier name'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Phone or WhatsApp'}),
            'country': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Country'}),
            'address': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Address'}),
            'product_types': forms.SelectMultiple(attrs={'class': 'form-select', 'size': 8}),
        }


class InboundOrderEditForm(forms.ModelForm):
    recorded_at = forms.DateTimeField(
        label='Recorded at',
        input_formats=['%Y-%m-%dT%H:%M'],
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}),
    )

    class Meta:
        model = InboundOrder
        fields = ['supplier', 'invoice_no', 'invoice_date', 'note']
        widgets = {
            'supplier': forms.Select(attrs={'class': 'form-select'}),
            'invoice_no': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Invoice number'}),
            'invoice_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'note': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Optional note'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and not self.initial.get('recorded_at'):
            local_dt = timezone.localtime(self.instance.created_at).replace(second=0, microsecond=0)
            self.initial['recorded_at'] = local_dt.strftime('%Y-%m-%dT%H:%M')


class InboundPurchaseLineForm(forms.ModelForm):
    class Meta:
        model = Purchase
        fields = ['quantity', 'cost_price']
        widgets = {
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'max': '100000'}),
            'cost_price': forms.NumberInput(attrs={'class': 'form-control', 'min': '0.01', 'step': '0.01', 'max': '999999.99'}),
        }

    def clean(self):
        cleaned = super().clean()
        quantity = cleaned.get('quantity')
        if quantity is None:
            return cleaned

        sold_units = max((self.instance.quantity or 0) - (self.instance.remaining or 0), 0)
        if cleaned.get('DELETE'):
            if sold_units > 0:
                raise forms.ValidationError('This line already has sold units, so it cannot be deleted.')
            return cleaned

        if quantity < sold_units:
            self.add_error('quantity', f'Cannot reduce below {sold_units} because those units were already sold.')
        return cleaned


InboundPurchaseFormSet = inlineformset_factory(
    InboundOrder,
    Purchase,
    form=InboundPurchaseLineForm,
    extra=0,
    can_delete=True,
)


class DirectPurchaseEditForm(forms.ModelForm):
    recorded_at = forms.DateTimeField(
        label='Recorded at',
        input_formats=['%Y-%m-%dT%H:%M'],
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}),
    )

    class Meta:
        model = Purchase
        fields = ['supplier', 'quantity', 'cost_price']
        widgets = {
            'supplier': forms.Select(attrs={'class': 'form-select'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'max': '100000'}),
            'cost_price': forms.NumberInput(attrs={'class': 'form-control', 'min': '0.01', 'step': '0.01', 'max': '999999.99'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and not self.initial.get('recorded_at'):
            local_dt = timezone.localtime(self.instance.date).replace(second=0, microsecond=0)
            self.initial['recorded_at'] = local_dt.strftime('%Y-%m-%dT%H:%M')

    def clean_quantity(self):
        quantity = self.cleaned_data['quantity']
        sold_units = max((self.instance.quantity or 0) - (self.instance.remaining or 0), 0)
        if quantity < sold_units:
            raise forms.ValidationError(f'Cannot reduce below {sold_units} because those units were already sold.')
        return quantity


class PrintProfileForm(forms.ModelForm):
    class Meta:
        model = PrintProfile
        fields = ['name', 'nif', 'phone', 'address', 'email', 'footer_note']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Store name'}),
            'nif': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Tax / NIF'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Phone'}),
            'address': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Address'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Email'}),
            'footer_note': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Footer note on printed receipt'}),
        }


class EmployeeAccountForm(forms.ModelForm):
    ROLE_CHOICES = [
        ('employee', 'Employee'),
        ('manager', 'Manager'),
    ]

    role = forms.ChoiceField(
        choices=ROLE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text='Managers can access sensitive business pages and stock tools.',
    )
    password1 = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Set password'}),
    )
    password2 = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Confirm password'}),
    )

    class Meta:
        model = get_user_model()
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Username'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'First name'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Last name'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Email'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['is_active'].widget.attrs.setdefault('class', 'form-check-input')
        if self.instance and self.instance.pk and not self.initial.get('role'):
            self.initial['role'] = 'manager' if self.instance.is_staff else 'employee'

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get('password1') or ''
        password2 = cleaned.get('password2') or ''

        if not self.instance.pk and not password1:
            self.add_error('password1', 'Password is required for a new employee account.')

        if password1 or password2:
            if password1 != password2:
                self.add_error('password2', 'Passwords do not match.')
            elif len(password1) < 8:
                self.add_error('password1', 'Password must be at least 8 characters.')
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        role = self.cleaned_data.get('role') or 'employee'
        user.is_superuser = False
        user.is_staff = role == 'manager'

        password1 = self.cleaned_data.get('password1')
        if password1:
            user.set_password(password1)

        if commit:
            user.save()
            managers_group, _created = Group.objects.get_or_create(name='Managers')
            if role == 'manager':
                user.groups.add(managers_group)
            else:
                user.groups.remove(managers_group)
            self.save_m2m()

        return user


class CustomerChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.name} ({obj.nif})"


class ProductChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.display_name} [{obj.barcode}]"


class SaleOrderCorrectionForm(forms.ModelForm):
    customer = CustomerChoiceField(
        queryset=Customer.objects.order_by('name'),
        required=False,
        empty_label='Walk-in / no customer',
    )
    order_datetime = forms.DateTimeField(
        input_formats=['%Y-%m-%dT%H:%M'],
        widget=forms.DateTimeInput(
            attrs={'type': 'datetime-local', 'class': 'form-control'},
            format='%Y-%m-%dT%H:%M',
        ),
    )
    reason = forms.CharField(
        required=True,
        max_length=255,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Why are you correcting this order?'}),
    )

    class Meta:
        model = SaleOrder
        fields = ['customer', 'note']
        widgets = {
            'note': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional internal note'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['customer'].widget.attrs.setdefault('class', 'form-select')
        if self.instance and self.instance.pk and not self.initial.get('order_datetime'):
            self.initial['order_datetime'] = timezone.localtime(self.instance.created_at).strftime('%Y-%m-%dT%H:%M')


class SaleCorrectionLineForm(forms.Form):
    product = ProductChoiceField(
        queryset=Product.objects.select_related('brand_master', 'series_master', 'category').order_by('brand', 'model', 'name'),
        required=False,
        widget=forms.HiddenInput(attrs={'class': 'js-product-id'}),
    )
    quantity = forms.IntegerField(
        min_value=1,
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
    )
    unit_price = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=0.01,
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0.01'}),
    )
    payment_method = forms.ChoiceField(
        choices=[('', 'Select payment')] + list(Sale.PAYMENT_METHOD_CHOICES),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('DELETE'):
            return cleaned

        product = cleaned.get('product')
        quantity = cleaned.get('quantity')
        unit_price = cleaned.get('unit_price')
        payment_method = cleaned.get('payment_method')

        if not any([product, quantity, unit_price, payment_method]):
            return cleaned
        if payment_method and not any([product, quantity, unit_price]):
            cleaned['payment_method'] = ''
            return cleaned

        values = {
            'product': product,
            'quantity': quantity,
            'unit_price': unit_price,
            'payment_method': payment_method,
        }
        missing = [label for label, value in values.items() if not value]
        if missing:
            raise forms.ValidationError('Each active line needs product, quantity, unit price, and payment method.')
        return cleaned


SaleCorrectionLineFormSet = formset_factory(SaleCorrectionLineForm, extra=1, can_delete=True)


class PurchaseForm(forms.ModelForm):
    class Meta:
        model = Purchase
        fields = ['product', 'supplier', 'quantity', 'cost_price']


class SaleForm(forms.ModelForm):
    class Meta:
        model = Sale
        fields = ['customer', 'quantity', 'unit_price', 'payment_method']
        widgets = {
            'unit_price': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01', 'max': '9999.99'}),
            'quantity': forms.NumberInput(attrs={'min': '1', 'max': '1000'}),
        }

    def clean_unit_price(self):
        v = self.cleaned_data['unit_price']
        if v <= 0 or v > 9999.99:
            raise forms.ValidationError("Price must be between 0.01 and 9999.99.")
        return v

    def clean_quantity(self):
        q = self.cleaned_data['quantity']
        if q < 1 or q > 1000:
            raise forms.ValidationError("Quantity must be between 1 and 1000.")
        return q


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ['nif', 'name', 'phone', 'email', 'notes']
        widgets = {'notes': forms.Textarea(attrs={'rows': 2})}


class ARInvoiceForm(forms.ModelForm):
    class Meta:
        model = ARInvoice
        fields = ['customer', 'due_date', 'note']
        widgets = {
            'due_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'note': forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}),
        }


class ARItemForm(forms.ModelForm):
    class Meta:
        model = ARItem
        fields = ['product_name', 'quantity', 'unit_price']
        widgets = {
            'product_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Type product name'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'unit_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0.01'}),
        }


ARItemFormSet = inlineformset_factory(
    ARInvoice, ARItem,
    form=ARItemForm,
    fields=['product_name', 'quantity', 'unit_price'],
    extra=3, can_delete=True, min_num=1, validate_min=True
)


class ARPaymentForm(forms.ModelForm):
    class Meta:
        model = ARPayment
        fields = ['amount', 'method', 'note']
        widgets = {
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0.01'}),
            'method': forms.Select(attrs={'class': 'form-select'}),
            'note': forms.TextInput(attrs={'class': 'form-control'}),
        }
