from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def reject_unmapped_legacy_data(apps, schema_editor):
    for model_name in ('Activity', 'Subtask', 'DailyCapacity'):
        model = apps.get_model('events', model_name)
        if model.objects.using(schema_editor.connection.alias).exists():
            raise RuntimeError(
                'No se pueden asignar automáticamente datos existentes a cuentas Django. '
                'Respalda o vacía estos datos, o define primero una migración de correspondencia.'
            )


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('events', '0002_activity_frontend_fields'),
    ]

    operations = [
        migrations.RunPython(reject_unmapped_legacy_data, migrations.RunPython.noop),
        migrations.AlterUniqueTogether(
            name='dailycapacity',
            unique_together=set(),
        ),
        migrations.RemoveField(
            model_name='activity',
            name='user_id',
        ),
        migrations.RemoveField(
            model_name='dailycapacity',
            name='user_id',
        ),
        migrations.AddField(
            model_name='activity',
            name='user',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='activities',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='subtask',
            name='user',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='subtasks',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='dailycapacity',
            name='user',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='daily_capacities',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='activity',
            name='user',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='activities',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='subtask',
            name='user',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='subtasks',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='dailycapacity',
            name='user',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='daily_capacities',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterUniqueTogether(
            name='dailycapacity',
            unique_together={('user', 'date')},
        ),
    ]
