from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Activity, Subtask

User = get_user_model()


class ExecutionRegistrationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='us09_user',
            email='us09@example.com',
            password='V3ry-long-Test-P@ssw0rd!',
        )
        self.client.force_authenticate(user=self.user)
        today = timezone.localdate()
        self.activity = Activity.objects.create(
            user=self.user,
            name='Evento US-09',
            date_event=today + timedelta(days=30),
        )
        self.subtask = Subtask.objects.create(
            user=self.user,
            activity=self.activity,
            name='Confirmar salón',
            due_date=self.activity.date_event,
            scheduled_date=today + timedelta(days=4),
            estimated_hours='2.0',
        )
        self.url = reverse('subtask-registrar-ejecucion', args=[self.subtask.id])

    def test_marks_subtask_done(self):
        response = self.client.patch(
            self.url,
            {'status': 'done'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.subtask.refresh_from_db()
        self.assertEqual(self.subtask.status, 'done')
        self.assertEqual(response.data['status'], 'done')

    def test_postpones_subtask_with_note_and_new_date(self):
        new_date = self.subtask.scheduled_date + timedelta(days=3)
        response = self.client.patch(
            self.url,
            {
                'status': 'postponed',
                'note': 'Esperando confirmación del salón',
                'due_date': new_date.isoformat(),
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.subtask.refresh_from_db()
        self.assertEqual(self.subtask.status, 'postponed')
        self.assertEqual(self.subtask.note, 'Esperando confirmación del salón')
        self.assertEqual(self.subtask.scheduled_date, new_date)
        self.assertEqual(self.subtask.due_date, self.activity.date_event)
        self.assertEqual(response.data['scheduled_date'], new_date.isoformat())

    def test_postponing_to_an_overloaded_day_is_rejected(self):
        new_date = self.subtask.scheduled_date + timedelta(days=3)
        type(self.subtask).objects.create(
            user=self.user,
            activity=self.activity,
            name='Otra gestión',
            due_date=new_date,
            scheduled_date=new_date,
            estimated_hours='5.0',
        )

        response = self.client.patch(
            self.url,
            {
                'status': 'postponed',
                'due_date': new_date.isoformat(),
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['conflict']['total_hours'], 7.0)
        self.subtask.refresh_from_db()
        self.assertEqual(self.subtask.status, 'pending')
        self.assertNotEqual(self.subtask.scheduled_date, new_date)

    def test_note_and_new_date_are_optional_when_postponing(self):
        original_date = self.subtask.scheduled_date
        response = self.client.patch(
            self.url,
            {'status': 'postponed'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.subtask.refresh_from_db()
        self.assertEqual(self.subtask.status, 'postponed')
        self.assertEqual(self.subtask.scheduled_date, original_date)

    def test_rejects_invalid_status_and_future_beyond_event_date(self):
        invalid_status = self.client.patch(
            self.url,
            {'status': 'pending'},
            format='json',
        )
        invalid_date = self.client.patch(
            self.url,
            {
                'status': 'postponed',
                'scheduled_date': (self.activity.date_event + timedelta(days=1)).isoformat(),
            },
            format='json',
        )

        self.assertEqual(invalid_status.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(invalid_date.status_code, status.HTTP_400_BAD_REQUEST)
        self.subtask.refresh_from_db()
        self.assertEqual(self.subtask.status, 'pending')

    def test_cannot_register_another_organizers_subtask(self):
        other = User.objects.create_user(
            username='us09_other',
            email='us09-other@example.com',
            password='V3ry-long-Test-P@ssw0rd!',
        )
        other_activity = Activity.objects.create(
            user=other,
            name='Evento ajeno',
            date_event=self.activity.date_event,
        )
        other_subtask = Subtask.objects.create(
            user=other,
            activity=other_activity,
            name='Tarea ajena',
            due_date=self.subtask.due_date,
            scheduled_date=self.subtask.scheduled_date,
            estimated_hours='1.0',
        )

        response = self.client.patch(
            reverse('subtask-registrar-ejecucion', args=[other_subtask.id]),
            {'status': 'done'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        other_subtask.refresh_from_db()
        self.assertEqual(other_subtask.status, 'pending')


class ActivityProgressTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='us10_user',
            email='us10@example.com',
            password='V3ry-long-Test-P@ssw0rd!',
        )
        self.client.force_authenticate(user=self.user)
        self.activity = Activity.objects.create(
            user=self.user,
            name='Evento US-10',
            date_event=timezone.localdate() + timedelta(days=30),
        )
        self.url = reverse('activity-progreso', args=[self.activity.id])

    def make_subtask(self, name, state, owner=None, activity=None):
        owner = owner or self.user
        activity = activity or self.activity
        return Subtask.objects.create(
            user=owner,
            activity=activity,
            name=name,
            due_date=self.activity.date_event - timedelta(days=1),
            scheduled_date=self.activity.date_event - timedelta(days=2),
            estimated_hours='1.0',
            status=state,
        )

    def test_empty_event_has_zero_progress_and_task_counts(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {
            'activity_id': self.activity.id,
            'progress': 0,
            'completed_subtasks': 0,
            'total_subtasks': 0,
        })

    def test_progress_counts_done_tasks_only_and_ignores_foreign_subtasks(self):
        self.make_subtask('Hecha', 'done')
        self.make_subtask('Pendiente', 'pending')
        self.make_subtask('En curso', 'in_progress')
        self.make_subtask('Pospuesta', 'postponed')

        other = User.objects.create_user(
            username='us10_other',
            email='us10-other@example.com',
            password='V3ry-long-Test-P@ssw0rd!',
        )
        self.make_subtask(
            'Subtarea ajena',
            'done',
            owner=other,
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['progress'], 25)
        self.assertEqual(response.data['completed_subtasks'], 1)
        self.assertEqual(response.data['total_subtasks'], 4)
