from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock', '0076_coffret_no_volume'),
    ]

    operations = [
        migrations.AddField(
            model_name='brand',
            name='logo',
            field=models.ImageField(blank=True, default='', upload_to='brand_logos/'),
        ),
    ]
