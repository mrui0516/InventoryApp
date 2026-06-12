from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock', '0021_attendancerecord'),
    ]

    operations = [
        migrations.CreateModel(
            name='PrintProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(default='KHAN PERFUME', max_length=120)),
                ('nif', models.CharField(blank=True, default='517067226', max_length=32)),
                ('phone', models.CharField(blank=True, default='(+351) 920 106 263', max_length=40)),
                ('address', models.CharField(blank=True, default='CENTRO BABILONIA LOJA 90A, AMADORA, 2700-337', max_length=255)),
                ('email', models.EmailField(blank=True, default='SADIWALIKHAN@YAHOO.COM', max_length=254)),
                ('footer_note', models.CharField(blank=True, default='Thank you for your purchase.', max_length=160)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Print profile',
                'verbose_name_plural': 'Print profile',
            },
        ),
    ]
