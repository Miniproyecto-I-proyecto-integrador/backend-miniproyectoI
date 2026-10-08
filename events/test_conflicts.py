from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Activity, Subtask
from .services import get_daily_limit, set_daily_limit

User = get_user_model()
PASSWORD = 'V3ry-long-Test-P@ssw0rd!'


class ConflictTestBase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user('organizer_a', 'a@example.com', PASSWORD)
        self.other = User.objects.create_user('organizer_b', 'b@example.com', PASSWORD)
        self.today = timezone.localdate()
        self.day_x = self.today + timedelta(days=5)
        self.day_y = self.today + timedelta(days=6)
        self.activity = Activity.objects.create(
            user=self.user, name='Boda', date_event=self.today + timedelta(days=30),
        )
        self.other_activity = Activity.objects.create(
            user=self.other, name='Ajeno', date_event=self.today + timedelta(days=30),
        )
        self.client.force_authenticate(user=self.user)

    def make(self, day, hours, status_='pending', user=None, activity=None, name='Gestión'):
        user = user or self.user
        activity = activity or (self.activity if user == self.user else self.other_activity)
        return Subtask.objects.create(
            user=user, activity=activity, name=name, due_date=day, scheduled_date=day,
            estimated_hours=Decimal(str(hours)), status=status_,
        )

    def patch(self, subtask, payload):
        return self.client.patch(reverse('subtask-detail', args=[subtask.id]), payload, format='json')

    def create_via_api(self, day, hours, **extra):
        payload = {
            'activity': self.activity.id, 'name': 'Nueva', 'due_date': day.isoformat(),
            'scheduled_date': day.isoformat(), 'estimated_hours': str(hours), **extra,
        }
        return self.client.post(reverse('subtask-list'), payload, format='json')


class OverloadDetectionTests(ConflictTestBase):
    """HU-07: detección de conflicto al reprogramar / crear gestiones."""

    def test_conflict_detected_with_backlog_message_and_figures(self):
        self.make(self.day_x, 5)
        proveedores = self.make(self.day_y, 2, name='Buscar proveedores')

        response = self.patch(proveedores, {'due_date': self.day_x.isoformat()})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data['conflicto'],
            ['Quedarías con 7h de gestión planificadas (límite 6h).'],
        )
        conflict = response.data['conflict']
        self.assertEqual(conflict['date'], self.day_x.isoformat())
        self.assertEqual(conflict['current_hours'], 5.0)
        self.assertEqual(conflict['proposed_hours'], 2.0)
        self.assertEqual(conflict['total_hours'], 7.0)
        self.assertEqual(conflict['limit_hours'], 6)
        self.assertEqual(conflict['excess_hours'], 1.0)
        self.assertEqual(conflict['options'], ['move', 'reduce_hours', 'postpone'])

    def test_conflicting_change_is_not_persisted(self):
        self.make(self.day_x, 5)
        proveedores = self.make(self.day_y, 2)

        self.patch(proveedores, {'due_date': self.day_x.isoformat()})

        proveedores.refresh_from_db()
        self.assertEqual(proveedores.due_date, self.day_y)

    def test_no_conflict_when_total_is_below_limit(self):
        self.make(self.day_x, 4)
        proveedores = self.make(self.day_y, 1)

        response = self.patch(proveedores, {'due_date': self.day_x.isoformat()})

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_exact_limit_is_not_a_conflict(self):
        self.make(self.day_x, 4)
        proveedores = self.make(self.day_y, 2)

        response = self.patch(proveedores, {'due_date': self.day_x.isoformat()})

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_day_without_previous_tasks_has_zero_current_hours(self):
        response = self.create_via_api(self.day_x, 2)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_single_task_above_limit_on_empty_day_is_a_conflict(self):
        response = self.create_via_api(self.day_x, 7)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['conflict']['current_hours'], 0.0)
        self.assertEqual(response.data['conflict']['total_hours'], 7.0)
        self.assertEqual(Subtask.objects.count(), 0)

    def test_only_the_affected_day_is_considered(self):
        self.make(self.day_y, 6)
        proveedores = self.make(self.today + timedelta(days=7), 2)

        response = self.patch(proveedores, {'due_date': self.day_x.isoformat()})

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_other_organizers_tasks_do_not_count(self):
        self.make(self.day_x, 6, user=self.other)

        response = self.create_via_api(self.day_x, 2)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_only_pending_and_in_progress_tasks_count(self):
        self.make(self.day_x, 3, status_='done')
        self.make(self.day_x, 3, status_='postponed')
        self.make(self.day_x, 2, status_='in_progress')
        self.make(self.day_x, 2, status_='pending')

        response = self.create_via_api(self.day_x, 3)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['conflict']['current_hours'], 4.0)
        self.assertEqual(response.data['conflict']['total_hours'], 7.0)

    def test_half_hours_are_formatted_without_trailing_zeros(self):
        self.make(self.day_x, 5.5)

        response = self.create_via_api(self.day_x, 1)

        self.assertEqual(
            response.data['conflicto'],
            ['Quedarías con 6.5h de gestión planificadas (límite 6h).'],
        )

    def test_uses_the_organizers_own_limit(self):
        set_daily_limit(self.user, 4)
        set_daily_limit(self.other, 10)
        self.make(self.day_x, 3)
        self.make(self.day_x, 3, user=self.other)

        mine = self.create_via_api(self.day_x, 2)

        self.assertEqual(mine.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(mine.data['conflict']['limit_hours'], 4)
        self.assertEqual(
            mine.data['conflicto'], ['Quedarías con 5h de gestión planificadas (límite 4h).'],
        )

    def test_simulated_calculation_error_does_not_persist_anything(self):
        proveedores = self.make(self.day_y, 2)
        self.client.raise_request_exception = False

        with mock.patch('events.serializers.check_day_capacity', side_effect=RuntimeError('boom')):
            response = self.patch(proveedores, {'due_date': self.day_x.isoformat()})

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        proveedores.refresh_from_db()
        self.assertEqual(proveedores.due_date, self.day_y)


class OverloadedDayStaysResolvableTests(ConflictTestBase):
    """Un día ya sobrecargado no bloquea ediciones que no suman carga."""

    def setUp(self):
        super().setUp()
        self.first = self.make(self.day_x, 4, name='Salón')
        self.second = self.make(self.day_x, 4, name='Catering')  # día con 8h, límite 6h

    def test_editing_other_fields_is_allowed(self):
        self.assertEqual(self.patch(self.first, {'note': 'llamar mañana'}).status_code, 200)
        self.assertEqual(self.patch(self.first, {'name': 'Salón principal'}).status_code, 200)

    def test_marking_done_or_postponed_is_allowed(self):
        self.assertEqual(self.patch(self.first, {'status': 'done'}).status_code, 200)
        self.assertEqual(self.patch(self.second, {'status': 'postponed'}).status_code, 200)

    def test_reducing_hours_is_allowed_even_if_conflict_persists(self):
        response = self.patch(self.first, {'estimated_hours': '3.0'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_increasing_hours_is_blocked(self):
        response = self.patch(self.first, {'estimated_hours': '4.5'})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['conflict']['total_hours'], 8.5)

    def test_moving_to_another_day_is_allowed(self):
        response = self.patch(self.first, {'due_date': self.day_y.isoformat()})

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_moving_to_a_full_day_is_blocked(self):
        self.make(self.day_y, 5)

        response = self.patch(self.first, {'due_date': self.day_y.isoformat()})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reactivating_a_postponed_task_on_a_full_day_is_blocked(self):
        postponed = self.make(self.day_x, 2, status_='postponed')

        response = self.patch(postponed, {'status': 'pending'})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['conflict']['total_hours'], 10.0)


class DailySummaryTests(ConflictTestBase):
    """El resumen usa due_date, estados activos y el límite del usuario."""

    def test_summary_counts_active_tasks_of_the_day(self):
        self.make(self.day_x, 3)
        self.make(self.day_x, 1.5, status_='in_progress')
        self.make(self.day_x, 4, status_='done')
        self.make(self.day_y, 2)

        response = self.client.get(reverse('daily-capacity-resumen'), {'date': self.day_x.isoformat()})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {
            'date': self.day_x.isoformat(),
            'assigned_hours': 4.5,
            'limit_hours': 6,
            'remaining_hours': 1.5,
            'overloaded': False,
        })

    def test_summary_flags_overload_only_above_the_limit(self):
        self.make(self.day_x, 6)
        exact = self.client.get(reverse('daily-capacity-resumen'), {'date': self.day_x.isoformat()})
        self.make(self.day_x, 0.5)
        over = self.client.get(reverse('daily-capacity-resumen'), {'date': self.day_x.isoformat()})

        self.assertFalse(exact.data['overloaded'])
        self.assertTrue(over.data['overloaded'])
        self.assertEqual(over.data['remaining_hours'], 0.0)

    def test_summary_defaults_to_today(self):
        self.make(self.today, 2)

        response = self.client.get(reverse('daily-capacity-resumen'))

        self.assertEqual(response.data['date'], self.today.isoformat())
        self.assertEqual(response.data['assigned_hours'], 2.0)

    def test_summary_uses_client_today_when_server_date_differs(self):
        client_today = self.today + timedelta(days=1)
        self.make(client_today, 2)

        response = self.client.get(
            reverse('daily-capacity-resumen'),
            {'today': client_today.isoformat()},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['date'], client_today.isoformat())
        self.assertEqual(response.data['assigned_hours'], 2.0)

    def test_summary_rejects_invalid_dates_without_server_error(self):
        for bad in ('abc', '2026-13-45', '31/10/2026'):
            response = self.client.get(reverse('daily-capacity-resumen'), {'date': bad})
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, bad)
            self.assertIn('date', response.data)


class LowerLimitWarningTests(ConflictTestBase):
    """HU-12: al bajar el límite se guarda y se avisa de los días en conflicto."""

    def setUp(self):
        super().setUp()
        self.url = reverse('daily-limit')

    def test_lowering_limit_saves_and_lists_overloaded_days_with_tasks(self):
        salon = self.make(self.day_x, 3, name='Salón')
        catering = self.make(self.day_x, 2.5, name='Catering')  # 5.5h el día X
        self.make(self.day_y, 2)  # 2h, sin conflicto

        response = self.client.put(self.url, {'daily_hours_limit': 5}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(get_daily_limit(self.user), 5)
        self.assertTrue(response.data['has_conflicts'])
        self.assertEqual(len(response.data['overloaded_days']), 1)
        day = response.data['overloaded_days'][0]
        self.assertEqual(day['date'], self.day_x.isoformat())
        self.assertEqual(day['total_hours'], 5.5)
        self.assertEqual(day['limit_hours'], 5)
        self.assertEqual(day['excess_hours'], 0.5)
        self.assertEqual([item['id'] for item in day['subtasks']], [salon.id, catering.id])
        self.assertEqual(day['subtasks'][0]['activity_name'], 'Boda')
        self.assertEqual(response.data['resolution_options'], ['move', 'reduce_hours', 'postpone'])

    def test_no_conflicts_returns_empty_list(self):
        self.make(self.day_x, 4)

        response = self.client.put(self.url, {'daily_hours_limit': 5}, format='json')

        self.assertFalse(response.data['has_conflicts'])
        self.assertEqual(response.data['overloaded_days'], [])

    def test_exact_limit_is_not_reported(self):
        self.make(self.day_x, 5)

        response = self.client.put(self.url, {'daily_hours_limit': 5}, format='json')

        self.assertEqual(response.data['overloaded_days'], [])

    def test_past_days_and_inactive_tasks_are_not_reported(self):
        self.make(self.today - timedelta(days=2), 8)
        self.make(self.day_x, 8, status_='done')
        self.make(self.day_y, 8, status_='postponed')

        response = self.client.put(self.url, {'daily_hours_limit': 4}, format='json')

        self.assertEqual(response.data['overloaded_days'], [])

    def test_other_organizers_tasks_are_not_reported(self):
        self.make(self.day_x, 8, user=self.other)

        response = self.client.put(self.url, {'daily_hours_limit': 4}, format='json')

        self.assertEqual(response.data['overloaded_days'], [])

    def test_get_also_reports_current_conflicts(self):
        self.make(self.day_x, 4)
        self.make(self.day_x, 3)

        response = self.client.get(self.url)

        self.assertTrue(response.data['has_conflicts'])
        self.assertEqual(response.data['overloaded_days'][0]['total_hours'], 7.0)

    def test_daily_limit_uses_client_today(self):
        client_today = self.today + timedelta(days=1)
        self.make(client_today, 8)

        response = self.client.get(
            self.url,
            {'today': client_today.isoformat()},
        )

        self.assertTrue(response.data['has_conflicts'])
        self.assertEqual(response.data['overloaded_days'][0]['date'], client_today.isoformat())

    def test_invalid_limit_is_not_saved_and_reports_nothing_new(self):
        self.make(self.day_x, 4)

        response = self.client.put(self.url, {'daily_hours_limit': 0}, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(get_daily_limit(self.user), 6)

    def test_conflict_can_be_resolved_after_lowering_the_limit(self):
        first = self.make(self.day_x, 3)
        second = self.make(self.day_x, 3)
        self.client.put(self.url, {'daily_hours_limit': 4}, format='json')

        self.assertEqual(self.patch(second, {'due_date': self.day_y.isoformat()}).status_code, 200)

        response = self.client.get(self.url)
        self.assertFalse(response.data['has_conflicts'])
        first.refresh_from_db()
        self.assertEqual(first.due_date, self.day_x)
