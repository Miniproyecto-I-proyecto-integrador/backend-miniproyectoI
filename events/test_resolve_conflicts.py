from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
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
        OrganizerSettings.objects.create(user=self.user, daily_hours_limit=6)
        self.activity = Activity.objects.create(
            user=self.user,
            name='Evento HU-08',
            date_event=date(2026, 10, 20),
        )
        self.target_day = date(2026, 10, 10)
        self.subtask = Subtask.objects.create(
            user=self.user,
            activity=self.activity,
            name='Buscar proveedor',
            due_date=self.target_day,
            scheduled_date=self.target_day,
            estimated_hours=Decimal('2.0'),
        )
        Subtask.objects.create(
            user=self.user,
            activity=self.activity,
            name='Gestión existente',
            due_date=self.target_day,
            scheduled_date=self.target_day,
            estimated_hours=Decimal('5.0'),
        )

    def test_suggests_first_available_day(self):
        response = self.client.get(
            reverse('subtask-sugerir-dia', args=[self.subtask.id])
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['suggestion']['date'], '2026-10-11')
        self.assertEqual(response.data['suggestion']['total_hours'], 2.0)
        self.assertEqual(response.data['suggestion']['limit_hours'], 6)

    def test_move_to_available_day_persists_and_resolves(self):
        response = self.client.patch(
            reverse('subtask-resolver-mover', args=[self.subtask.id]),
            {'due_date': '2026-10-11'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['conflict_resolved'])
        self.subtask.refresh_from_db()
        self.assertEqual(self.subtask.due_date, date(2026, 10, 11))

    def test_move_to_day_without_capacity_is_rejected_and_not_saved(self):
        Subtask.objects.create(
            user=self.user,
            activity=self.activity,
            name='Otra gestión',
            due_date=date(2026, 10, 12),
            scheduled_date=date(2026, 10, 12),
            estimated_hours=Decimal('5.0'),
        )

        response = self.client.patch(
            reverse('subtask-resolver-mover', args=[self.subtask.id]),
            {'due_date': '2026-10-12'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('conflict', response.data)
        self.subtask.refresh_from_db()
        self.assertEqual(self.subtask.due_date, self.target_day)

    def test_reduce_hours_resolves_conflict(self):
        response = self.client.patch(
            reverse('subtask-resolver-reducir', args=[self.subtask.id]),
            {'estimated_hours': '1.0'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['conflict_resolved'])
        self.assertFalse(response.data['conflict_persists'])
        self.subtask.refresh_from_db()
        self.assertEqual(self.subtask.estimated_hours, Decimal('1.0'))

    def test_reduce_hours_can_persist_when_conflict_remains(self):
        response = self.client.patch(
            reverse('subtask-resolver-reducir', args=[self.subtask.id]),
            {'estimated_hours': '1.5'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['conflict_resolved'])
        self.assertTrue(response.data['conflict_persists'])
        self.assertEqual(response.data['conflict']['total_hours'], 6.5)
        self.assertEqual(response.data['conflict']['limit_hours'], 6)
        self.subtask.refresh_from_db()
        self.assertEqual(self.subtask.estimated_hours, Decimal('1.5'))

    def test_reduce_hours_must_be_positive_and_lower_than_current(self):
        zero_response = self.client.patch(
            reverse('subtask-resolver-reducir', args=[self.subtask.id]),
            {'estimated_hours': '0'},
            format='json',
        )
        same_response = self.client.patch(
            reverse('subtask-resolver-reducir', args=[self.subtask.id]),
            {'estimated_hours': '2.0'},
            format='json',
        )

        self.assertEqual(zero_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(same_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.subtask.refresh_from_db()
        self.assertEqual(self.subtask.estimated_hours, Decimal('2.0'))
