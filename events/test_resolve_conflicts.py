from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Activity, OrganizerSettings, Subtask

User = get_user_model()


class HU08ResolutionTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='hu08_user',
            email='hu08@example.com',
            password='V3ry-long-Test-P@ssw0rd!',
        )
        self.client.force_authenticate(user=self.user)
        OrganizerSettings.objects.create(
            user=self.user,
            daily_hours_limit=6,
        )

        self.today = timezone.localdate()

        # La fecha límite original queda varios días después de hoy para
        # comprobar que sugerir-dia puede encontrar una fecha anterior a ella.
        self.target_day = self.today + timedelta(days=5)

        self.activity = Activity.objects.create(
            user=self.user,
            name='Evento HU-08',
            date_event=self.today + timedelta(days=12),
        )

        self.subtask = Subtask.objects.create(
            user=self.user,
            activity=self.activity,
            name='Buscar proveedor',
            due_date=self.target_day,
            scheduled_date=self.target_day,
            estimated_hours=Decimal('2.0'),
        )

        # El día de la fecha límite original está sobrecargado.
        Subtask.objects.create(
            user=self.user,
            activity=self.activity,
            name='Gestión existente',
            due_date=self.target_day,
            scheduled_date=self.target_day,
            estimated_hours=Decimal('5.0'),
        )

    def test_suggests_first_available_day_after_today_before_original_due_date(self):
        response = self.client.get(
            reverse(
                'subtask-sugerir-dia',
                args=[self.subtask.id],
            ),
            {'today': self.today.isoformat()},
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        expected_day = self.today + timedelta(days=1)

        self.assertEqual(
            response.data['suggestion']['date'],
            expected_day.isoformat(),
        )
        self.assertEqual(
            response.data['suggestion']['total_hours'],
            2.0,
        )
        self.assertEqual(
            response.data['suggestion']['limit_hours'],
            6,
        )

        # La sugerencia debe ser anterior a la fecha límite original.
        self.assertLess(
            expected_day,
            self.target_day,
        )

    def test_suggests_day_after_original_due_date_when_no_capacity_before(self):
        # Todos los días desde mañana hasta la fecha límite original quedan
        # ocupados con 5 h, por lo que la gestión de 2 h no cabe.
        day = self.today + timedelta(days=1)

        while day <= self.target_day:
            Subtask.objects.create(
                user=self.user,
                activity=self.activity,
                name=f'Gestión ocupada {day}',
                due_date=day,
                scheduled_date=day,
                estimated_hours=Decimal('5.0'),
            )
            day += timedelta(days=1)

        response = self.client.get(
            reverse(
                'subtask-sugerir-dia',
                args=[self.subtask.id],
            ),
            {'today': self.today.isoformat()},
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        expected_day = self.target_day + timedelta(days=1)

        self.assertEqual(
            response.data['suggestion']['date'],
            expected_day.isoformat(),
        )
        self.assertEqual(
            response.data['suggestion']['total_hours'],
            2.0,
        )
        self.assertEqual(
            response.data['suggestion']['limit_hours'],
            6,
        )

    def test_move_to_available_day_persists_and_resolves(self):
        new_day = self.target_day + timedelta(days=1)

        response = self.client.patch(
            reverse(
                'subtask-resolver-mover',
                args=[self.subtask.id],
            ) + f'?today={self.today.isoformat()}',
            {'due_date': new_day.isoformat()},
            format='json',
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertTrue(response.data['conflict_resolved'])
        self.assertFalse(response.data['conflict_persists'])

        self.subtask.refresh_from_db()

        self.assertEqual(
            self.subtask.due_date,
            new_day,
        )

    def test_move_reports_origin_conflict_if_it_still_remains_overloaded(self):
        Subtask.objects.create(
            user=self.user,
            activity=self.activity,
            name='Otra gestión',
            due_date=self.target_day,
            scheduled_date=self.target_day,
            estimated_hours=Decimal('2.0'),
        )

        new_day = self.target_day + timedelta(days=1)

        response = self.client.patch(
            reverse(
                'subtask-resolver-mover',
                args=[self.subtask.id],
            ),
            {'due_date': new_day.isoformat()},
            format='json',
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertFalse(response.data['conflict_resolved'])
        self.assertTrue(response.data['conflict_persists'])
        self.assertEqual(
            response.data['conflict']['date'],
            self.target_day.isoformat(),
        )
        self.assertEqual(
            response.data['conflict']['total_hours'],
            7.0,
        )
        self.assertEqual(
            response.data['conflict']['limit_hours'],
            6,
        )

        self.subtask.refresh_from_db()

        self.assertEqual(
            self.subtask.due_date,
            new_day,
        )

    def test_move_to_day_without_capacity_is_rejected_and_not_saved(self):
        Subtask.objects.create(
            user=self.user,
            activity=self.activity,
            name='Otra gestión',
            due_date=self.target_day + timedelta(days=1),
            scheduled_date=self.target_day + timedelta(days=1),
            estimated_hours=Decimal('5.0'),
        )

        new_day = self.target_day + timedelta(days=1)

        response = self.client.patch(
            reverse(
                'subtask-resolver-mover',
                args=[self.subtask.id],
            ),
            {'due_date': new_day.isoformat()},
            format='json',
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertIn('conflict', response.data)

        self.subtask.refresh_from_db()

        self.assertEqual(
            self.subtask.due_date,
            self.target_day,
        )

    def test_reduce_hours_resolves_conflict(self):
        response = self.client.patch(
            reverse(
                'subtask-resolver-reducir',
                args=[self.subtask.id],
            ),
            {'estimated_hours': '1.0'},
            format='json',
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertTrue(response.data['conflict_resolved'])
        self.assertFalse(response.data['conflict_persists'])

        self.subtask.refresh_from_db()

        self.assertEqual(
            self.subtask.estimated_hours,
            Decimal('1.0'),
        )

    def test_reduce_hours_can_persist_when_conflict_remains(self):
        response = self.client.patch(
            reverse(
                'subtask-resolver-reducir',
                args=[self.subtask.id],
            ),
            {'estimated_hours': '1.5'},
            format='json',
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertFalse(response.data['conflict_resolved'])
        self.assertTrue(response.data['conflict_persists'])
        self.assertEqual(
            response.data['conflict']['total_hours'],
            6.5,
        )
        self.assertEqual(
            response.data['conflict']['limit_hours'],
            6,
        )

        self.subtask.refresh_from_db()

        self.assertEqual(
            self.subtask.estimated_hours,
            Decimal('1.5'),
        )

    def test_reduce_hours_accepts_one_tenth_hour(self):
        response = self.client.patch(
            reverse(
                'subtask-resolver-reducir',
                args=[self.subtask.id],
            ),
            {'estimated_hours': '0.1'},
            format='json',
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.subtask.refresh_from_db()

        self.assertEqual(
            self.subtask.estimated_hours,
            Decimal('0.1'),
        )

    def test_reduce_hours_rejects_zero(self):
        response = self.client.patch(
            reverse(
                'subtask-resolver-reducir',
                args=[self.subtask.id],
            ),
            {'estimated_hours': '0.0'},
            format='json',
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertIn(
            'estimated_hours',
            response.data,
        )

    def test_reduce_hours_must_be_lower_than_current(self):
        response = self.client.patch(
            reverse(
                'subtask-resolver-reducir',
                args=[self.subtask.id],
            ),
            {'estimated_hours': '2.0'},
            format='json',
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

        self.subtask.refresh_from_db()

        self.assertEqual(
            self.subtask.estimated_hours,
            Decimal('2.0'),
        )

    def test_reprogramming_to_client_past_is_allowed(self):
        response = self.client.patch(
            reverse(
                'subtask-detail',
                args=[self.subtask.id],
            ),
            {
                'due_date': (
                    self.today - timedelta(days=1)
                ).isoformat(),
            },
            format='json',
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            response.data,
        )

        self.subtask.refresh_from_db()

        self.assertEqual(
            self.subtask.due_date,
            self.today - timedelta(days=1),
        )