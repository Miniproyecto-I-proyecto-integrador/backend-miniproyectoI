from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Activity, Subtask

User = get_user_model()
PASSWORD = 'V3ry-long-Test-P@ssw0rd!'


class CriticalFixesTests(APITestCase):
    """Correcciones: gestiones vencidas, fechas y scheduled_date."""

    def setUp(self):
        self.user = User.objects.create_user('org', 'org@example.com', PASSWORD)
        self.client.force_authenticate(user=self.user)
        self.today = timezone.localdate()
        self.event_day = self.today + timedelta(days=15)
        self.activity = Activity.objects.create(
            user=self.user, name='Boda', date_event=self.event_day,
        )

    def make_subtask(self, due_offset, scheduled_offset=None, hours=2, **extra):
        scheduled_offset = due_offset if scheduled_offset is None else scheduled_offset
        return Subtask.objects.create(
            user=self.user, activity=self.activity, name='Gestión',
            due_date=self.today + timedelta(days=due_offset),
            scheduled_date=self.today + timedelta(days=scheduled_offset),
            estimated_hours=hours, **extra,
        )

    def payload(self, **overrides):
        data = {
            'activity': self.activity.id,
            'name': 'Reservar salón',
            'due_date': (self.today + timedelta(days=5)).isoformat(),
            'scheduled_date': (self.today + timedelta(days=4)).isoformat(),
            'estimated_hours': '2.0',
        }
        data.update(overrides)
        return data

    # --- Error 1: gestiones vencidas ---------------------------------------
    def test_overdue_subtask_can_be_marked_done(self):
        subtask = self.make_subtask(-2)
        response = self.client.patch(
            f'/api/subtareas/{subtask.id}/', {'status': 'done'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        subtask.refresh_from_db()
        self.assertEqual(subtask.status, 'done')

    def test_overdue_subtask_can_be_edited_with_put_keeping_dates(self):
        subtask = self.make_subtask(-2)
        response = self.client.put(
            f'/api/subtareas/{subtask.id}/',
            {
                'activity': self.activity.id, 'name': 'Nuevo nombre',
                'due_date': subtask.due_date.isoformat(),
                'scheduled_date': subtask.scheduled_date.isoformat(),
                'estimated_hours': '2.0',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())

    def test_overdue_subtask_can_reduce_hours(self):
        subtask = self.make_subtask(-2, hours=4)
        response = self.client.patch(
            f'/api/subtareas/{subtask.id}/resolver-reducir/',
            {'estimated_hours': '1.0'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())

    def test_create_with_past_due_and_scheduled_dates_is_allowed(self):
        response = self.client.post('/api/subtareas/', self.payload(
            due_date=(self.today - timedelta(days=1)).isoformat(),
            scheduled_date=(self.today - timedelta(days=1)).isoformat(),
        ), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.json())

    def test_changing_due_date_to_the_past_is_allowed(self):
        subtask = self.make_subtask(5)
        response = self.client.patch(
            f'/api/subtareas/{subtask.id}/',
            {
                'due_date': (self.today - timedelta(days=1)).isoformat(),
                'scheduled_date': (self.today - timedelta(days=1)).isoformat(),
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())

    def test_move_to_past_date_is_allowed(self):
        subtask = self.make_subtask(5)
        response = self.client.patch(
            f'/api/subtareas/{subtask.id}/resolver-mover/',
            {'due_date': (self.today - timedelta(days=1)).isoformat()},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())

    # --- Error 3: fechas inexistentes en query params -----------------------
    def test_sugerir_dia_nonexistent_date_names_the_parameter(self):
        subtask = self.make_subtask(5)
        response = self.client.get(
            f'/api/subtareas/{subtask.id}/sugerir-dia/?today=2026-02-30',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(list(response.json().keys()), ['today'])

    def test_sugerir_dia_malformed_date_names_the_parameter(self):
        subtask = self.make_subtask(5)
        response = self.client.get(
            f'/api/subtareas/{subtask.id}/sugerir-dia/?desde=hoy',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(list(response.json().keys()), ['desde'])

    def test_limite_diario_and_resumen_nonexistent_date_return_400(self):
        self.assertEqual(
            self.client.get('/api/configuracion/limite-diario/?today=2026-13-01').status_code, 400)
        response = self.client.get('/api/capacidad-diaria/resumen/?date=2026-02-30')
        self.assertEqual(response.status_code, 400)
        self.assertIn('date', response.json())

    def test_sugerir_dia_valid_date_still_works(self):
        subtask = self.make_subtask(5)
        response = self.client.get(
            f'/api/subtareas/{subtask.id}/sugerir-dia/?today={self.today.isoformat()}',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    # scheduled_date is retained for compatibility but does not control scheduling.
    def test_scheduled_after_due_is_allowed_on_create(self):
        response = self.client.post('/api/subtareas/', self.payload(
            due_date=(self.today + timedelta(days=3)).isoformat(),
            scheduled_date=(self.today + timedelta(days=9)).isoformat(),
        ), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.json())

    def test_scheduled_in_the_past_is_allowed_on_create(self):
        response = self.client.post('/api/subtareas/', self.payload(
            scheduled_date=(self.today - timedelta(days=3)).isoformat(),
        ), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.json())

    def test_scheduled_after_due_is_allowed_on_patch(self):
        subtask = self.make_subtask(5, 4)
        response = self.client.patch(
            f'/api/subtareas/{subtask.id}/',
            {'scheduled_date': (self.today + timedelta(days=8)).isoformat()},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())

    def test_valid_create_still_works(self):
        response = self.client.post('/api/subtareas/', self.payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.json())

    def test_scheduled_equal_to_due_is_valid(self):
        day = (self.today + timedelta(days=5)).isoformat()
        response = self.client.post('/api/subtareas/', self.payload(
            due_date=day, scheduled_date=day), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.json())

    def test_move_earlier_does_not_change_scheduled_date(self):
        subtask = self.make_subtask(due_offset=8, scheduled_offset=7)
        original_scheduled = subtask.scheduled_date
        new_due = self.today + timedelta(days=3)
        response = self.client.patch(
            f'/api/subtareas/{subtask.id}/resolver-mover/',
            {'due_date': new_due.isoformat()}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        subtask.refresh_from_db()
        self.assertEqual(subtask.due_date, new_due)
        self.assertEqual(subtask.scheduled_date, original_scheduled)

    def test_move_later_keeps_scheduled_untouched(self):
        subtask = self.make_subtask(due_offset=5, scheduled_offset=4)
        original_scheduled = subtask.scheduled_date
        response = self.client.patch(
            f'/api/subtareas/{subtask.id}/resolver-mover/',
            {'due_date': (self.today + timedelta(days=9)).isoformat()}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        subtask.refresh_from_db()
        self.assertEqual(subtask.scheduled_date, original_scheduled)
