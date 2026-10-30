from django.db import migrations


def set_event_deadlines(apps, schema_editor):
    Subtask = apps.get_model('events', 'Subtask')
    database = schema_editor.connection.alias
    pending = []
    for subtask in (
        Subtask.objects.using(database)
        .select_related('activity')
        .iterator()
    ):
        if subtask.due_date != subtask.activity.date_event:
            subtask.due_date = subtask.activity.date_event
            pending.append(subtask)
        if len(pending) >= 500:
            Subtask.objects.using(database).bulk_update(pending, ['due_date'])
            pending.clear()
    if pending:
        Subtask.objects.using(database).bulk_update(pending, ['due_date'])


class Migration(migrations.Migration):
    dependencies = [
        ('events', '0004_organizersettings'),
    ]

    operations = [
        migrations.RunPython(set_event_deadlines, migrations.RunPython.noop),
    ]
