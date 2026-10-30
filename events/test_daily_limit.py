from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Activity, OrganizerSettings, Subtask
from .services import get_daily_limit, set_daily_limit

User = get_user_model()
PASSWORD = 'V3ry-long-Test-P@ssw0rd!'


class DailyLimitEndpointTests(APITestCase):
    """HU-12: GET/PUT/PATCH del límite diario de horas de gestión."""

    def setUp(self):
        self.user_a = User.objects.create_user('organizer_a', 'a@example.com', PASSWORD)
        self.user_b = User.objects.create_user('organizer_b', 'b@example.com', PASSWORD)
        self.url = reverse('daily-limit')
        self.client.force_authenticate(user=self.user_a)

    def test_requires_authentication(self):
        self.client.force_authenticate(user=None)

        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(
            self.client.put(self.url, {'daily_hours_limit': 4}, format='json').status_code,
            status.HTTP_401_UNAUTHORIZED,
        )
        self.assertEqual(
            self.client.patch(self.url, {'daily_hours_limit': 4}, format='json').status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_get_returns_default_6_when_nothing_saved_and_does_not_write(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['daily_hours_limit'], 6)
        self.assertTrue(response.data['is_default'])
        self.assertEqual(response.data['min_hours'], 1)
        self.assertEqual(response.data['max_hours'], 16)
        self.assertEqual(OrganizerSettings.objects.count(), 0)

    def test_put_updates_limit_and_get_reflects_it(self):
        put_response = self.client.put(self.url, {'daily_hours_limit': 4}, format='json')
        get_response = self.client.get(self.url)

        self.assertEqual(put_response.status_code, status.HTTP_200_OK)
        self.assertEqual(put_response.data['daily_hours_limit'], 4)
        self.assertFalse(put_response.data['is_default'])
        self.assertEqual(get_response.data['daily_hours_limit'], 4)
        self.assertEqual(OrganizerSettings.objects.get(user=self.user_a).daily_hours_limit, 4)

    def test_patch_updates_limit(self):
        response = self.client.patch(self.url, {'daily_hours_limit': 10}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(get_daily_limit(self.user_a), 10)

    def test_updating_twice_keeps_a_single_row(self):
        self.client.put(self.url, {'daily_hours_limit': 4}, format='json')
        self.client.put(self.url, {'daily_hours_limit': 8}, format='json')

        self.assertEqual(OrganizerSettings.objects.filter(user=self.user_a).count(), 1)
        self.assertEqual(get_daily_limit(self.user_a), 8)

    def test_range_boundaries_1_and_16_are_valid(self):
        for hours in (1, 16):
            response = self.client.put(self.url, {'daily_hours_limit': hours}, format='json')
            self.assertEqual(response.status_code, status.HTTP_200_OK, hours)
            self.assertEqual(get_daily_limit(self.user_a), hours)

    def test_out_of_range_values_are_rejected_with_clear_message_and_not_saved(self):
        self.client.put(self.url, {'daily_hours_limit': 5}, format='json')

        for bad in (0, -3, 17, 100):
            response = self.client.put(self.url, {'daily_hours_limit': bad}, format='json')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, bad)
            self.assertIn('entre 1 y 16', str(response.data['daily_hours_limit'][0]))

        self.assertEqual(get_daily_limit(self.user_a), 5)

    def test_invalid_types_and_missing_value_are_rejected(self):
        for payload in ({'daily_hours_limit': 'abc'}, {'daily_hours_limit': 5.5},
                        {'daily_hours_limit': None}, {'daily_hours_limit': ''}, {}):
            response = self.client.put(self.url, payload, format='json')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, payload)
            self.assertIn('daily_hours_limit', response.data, payload)

        self.assertEqual(OrganizerSettings.objects.count(), 0)

    def test_limit_is_stored_per_user(self):
        set_daily_limit(self.user_a, 6)
        set_daily_limit(self.user_b, 4)

        self.client.force_authenticate(user=self.user_b)
        self.assertEqual(self.client.get(self.url).data['daily_hours_limit'], 4)
        self.client.force_authenticate(user=self.user_a)
        self.assertEqual(self.client.get(self.url).data['daily_hours_limit'], 6)

    def test_updating_own_limit_never_changes_another_users_limit(self):
        set_daily_limit(self.user_b, 4)

        response = self.client.put(self.url, {
            'daily_hours_limit': 9,
            'user': self.user_b.id,
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(get_daily_limit(self.user_a), 9)
        self.assertEqual(get_daily_limit(self.user_b), 4)

    def test_database_constraint_rejects_out_of_range_values(self):
        for bad in (0, 17):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    OrganizerSettings.objects.create(user=self.user_a, daily_hours_limit=bad)


class DailyLimitAffectsConflictDetectionTests(APITestCase):
    """HU-12 escenario 2: US-07 se evalúa con el límite actualizado."""

    def setUp(self):
        self.user = User.objects.create_user('organizer_a', 'a@example.com', PASSWORD)
        self.activity = Activity.objects.create(
            user=self.user, name='Evento', date_event=date(2026, 10, 30),
        )
        self.client.force_authenticate(user=self.user)

    def _create_subtask(self, hours):
        return self.client.post(reverse('subtask-list'), {
            'activity': self.activity.id,
            'name': 'Gestión',
            'due_date': '2026-10-20',
            'scheduled_date': '2026-10-10',
            'estimated_hours': hours,
        }, format='json')

    def test_default_limit_is_6_for_conflict_detection(self):
        self.assertEqual(self._create_subtask('6.0').status_code, status.HTTP_201_CREATED)
        self.assertEqual(self._create_subtask('0.5').status_code, status.HTTP_400_BAD_REQUEST)

    def test_lowered_limit_is_used_by_conflict_detection(self):
        set_daily_limit(self.user, 4)

        response = self._create_subtask('5.0')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('límite 4', str(response.data['conflicto'][0]))
        self.assertEqual(Subtask.objects.count(), 0)

    def test_raised_limit_allows_more_hours(self):
        set_daily_limit(self.user, 10)

        self.assertEqual(self._create_subtask('8.0').status_code, status.HTTP_201_CREATED)

    def test_resumen_uses_user_limit(self):
        set_daily_limit(self.user, 4)
        Subtask.objects.create(
            user=self.user, activity=self.activity, name='Gestión',
            due_date=date(2026, 10, 20), scheduled_date=date(2026, 10, 10),
            estimated_hours=Decimal('5.0'),
        )

        response = self.client.get(reverse('daily-capacity-resumen'), {'date': '2026-10-10'})

        self.assertEqual(response.data['limit_hours'], 4)
        self.assertTrue(response.data['overloaded'])


class DailyLimitConflictWorkflowTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user('workflow_user', 'workflow@example.com', PASSWORD)
        self.client.force_authenticate(user=self.user)
        self.today = date(2026, 10, 16)
        self.activity = Activity.objects.create(
            user=self.user,
            name='Evento',
            date_event=date(2026, 10, 30),
        )
        self.conflict_day = date(2026, 10, 20)
        self.decoration = self.make_subtask('Decoración', self.conflict_day, '3.0')
        self.food = self.make_subtask('Comida', self.conflict_day, '3.0')
        self.preview_url = reverse('daily-limit-preview')
        self.apply_url = reverse('daily-limit-apply')

    def make_subtask(self, name, due_date, hours):
        return Subtask.objects.create(
            user=self.user,
            activity=self.activity,
            name=name,
            due_date=self.activity.date_event,
            scheduled_date=due_date,
            estimated_hours=Decimal(hours),
        )

    def preview(self, limit=5):
        return self.client.post(
            self.preview_url,
            {'daily_hours_limit': limit, 'today': self.today.isoformat()},
            format='json',
        )

    def test_preview_returns_conflicts_and_available_dates_without_saving(self):
        response = self.preview()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['has_conflicts'])
        day = response.data['overloaded_days'][0]
        self.assertEqual(day['total_hours'], 6.0)
        self.assertEqual(day['excess_hours'], 1.0)
        self.assertTrue(day['subtasks'][0]['suggested_dates'])
        self.assertEqual(
            day['subtasks'][0]['suggested_dates'][0]['total_hours'],
            3.0,
        )
        self.assertEqual(get_daily_limit(self.user), 6)
        self.assertEqual(self.decoration.due_date, self.activity.date_event)

    def test_apply_reduces_hours_and_saves_limit_together(self):
        response = self.client.post(
            self.apply_url,
            {
                'daily_hours_limit': 5,
                'today': self.today.isoformat(),
                'changes': [{
                    'subtask_id': self.food.id,
                    'action': 'reduce_hours',
                    'estimated_hours': '2.0',
                }],
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertFalse(response.data['has_conflicts'])
        self.assertEqual(get_daily_limit(self.user), 5)
        self.food.refresh_from_db()
        self.assertEqual(self.food.estimated_hours, Decimal('2.0'))

    def test_apply_moves_and_reduces_in_same_transaction(self):
        next_day = date(2026, 10, 21)
        response = self.client.post(
            self.apply_url,
            {
                'daily_hours_limit': 5,
                'today': self.today.isoformat(),
                'changes': [
                    {
                        'subtask_id': self.decoration.id,
                        'action': 'move',
                        'due_date': next_day.isoformat(),
                    },
                    {
                        'subtask_id': self.food.id,
                        'action': 'reduce_hours',
                        'estimated_hours': '2.0',
                    },
                ],
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.decoration.refresh_from_db()
        self.food.refresh_from_db()
        self.assertEqual(self.decoration.scheduled_date, next_day)
        self.assertEqual(self.decoration.due_date, self.activity.date_event)
        self.assertEqual(self.food.estimated_hours, Decimal('2.0'))
        self.assertEqual(get_daily_limit(self.user), 5)

    def test_unresolved_conflict_leaves_limit_and_subtasks_unchanged(self):
        other_day = date(2026, 10, 22)
        third = self.make_subtask('Flores', other_day, '3.0')
        fourth = self.make_subtask('Música', other_day, '3.0')

        response = self.client.post(
            self.apply_url,
            {
                'daily_hours_limit': 5,
                'today': self.today.isoformat(),
                'changes': [{
                    'subtask_id': self.food.id,
                    'action': 'reduce_hours',
                    'estimated_hours': '2.0',
                }],
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(response.data['overloaded_days'])
        self.assertEqual(get_daily_limit(self.user), 6)
        self.food.refresh_from_db()
        third.refresh_from_db()
        fourth.refresh_from_db()
        self.assertEqual(self.food.estimated_hours, Decimal('3.0'))
        self.assertEqual(third.scheduled_date, other_day)
        self.assertEqual(fourth.scheduled_date, other_day)
