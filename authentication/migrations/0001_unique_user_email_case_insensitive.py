from django.conf import settings
from django.db import migrations
from django.db.models import Count
from django.db.models.functions import Lower


INDEX_NAME = 'auth_user_email_ci_unique'


def create_email_index(apps, schema_editor):
    if schema_editor.connection.vendor not in {'postgresql', 'sqlite'}:
        raise RuntimeError(
            'La unicidad de correo requiere un índice único funcional '
            'con soporte para índices parciales.'
        )

    app_label, model_name = settings.AUTH_USER_MODEL.split('.')
    user_model = apps.get_model(app_label, model_name)
    duplicates_exist = (
        user_model.objects.using(schema_editor.connection.alias)
        .exclude(email='')
        .annotate(normalized_email=Lower('email'))
        .values('normalized_email')
        .annotate(email_count=Count('pk'))
        .filter(email_count__gt=1)
        .exists()
    )
    if duplicates_exist:
        raise RuntimeError(
            'No se puede habilitar la unicidad del correo: ya existen cuentas '
            'con correos duplicados ignorando mayúsculas. Resuelve esos '
            'duplicados antes de aplicar esta migración.'
        )

    table = schema_editor.quote_name(user_model._meta.db_table)
    email_column = schema_editor.quote_name(
        user_model._meta.get_field('email').column,
    )
    index_name = schema_editor.quote_name(INDEX_NAME)
    schema_editor.execute(
        f'CREATE UNIQUE INDEX {index_name} '
        f'ON {table} (LOWER({email_column})) '
        f"WHERE {email_column} <> ''"
    )


def drop_email_index(apps, schema_editor):
    index_name = schema_editor.quote_name(INDEX_NAME)
    schema_editor.execute(f'DROP INDEX {index_name}')


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(create_email_index, drop_email_index),
    ]
