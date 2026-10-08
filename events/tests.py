from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Activity, DailyCapacity, Subtask

User = get_user_model()


class EventOwnershipTests(APITestCase):
	def setUp(self):
		self.owner = User.objects.create_user(
			username='organizer_a',
			email='a@example.com',
			password='V3ry-long-Test-P@ssw0rd!',
		)
		self.other_owner = User.objects.create_user(
			username='organizer_b',
			email='b@example.com',
			password='V3ry-long-Test-P@ssw0rd!',
		)
		self.own_activity = Activity.objects.create(
			user=self.owner,
			name='Evento propio',
			date_event=date(2026, 10, 5),
		)
		self.other_activity = Activity.objects.create(
			user=self.other_owner,
			name='Evento ajeno',
			date_event=date(2026, 10, 5),
		)
		self.client.force_authenticate(user=self.owner)

	def test_event_endpoints_require_authentication(self):
		self.client.force_authenticate(user=None)

		response = self.client.get(reverse('activity-list'))

		self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

	def test_activity_list_and_detail_are_scoped_to_authenticated_owner(self):
		response = self.client.get(reverse('activity-list'))

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual([item['id'] for item in response.data], [self.own_activity.id])
		self.assertEqual(
			self.client.get(reverse('activity-detail', args=[self.other_activity.id])).status_code,
			status.HTTP_404_NOT_FOUND,
		)

	def test_foreign_activity_cannot_be_updated_or_deleted(self):
		detail_url = reverse('activity-detail', args=[self.other_activity.id])

		update_response = self.client.patch(detail_url, {'name': 'Intento de edición'}, format='json')
		delete_response = self.client.delete(detail_url)

		self.assertEqual(update_response.status_code, status.HTTP_404_NOT_FOUND)
		self.assertEqual(delete_response.status_code, status.HTTP_404_NOT_FOUND)
		self.assertTrue(Activity.objects.filter(pk=self.other_activity.id).exists())

	def test_activity_create_ignores_client_supplied_owner(self):
		response = self.client.post(reverse('activity-list'), {
			'name': 'Nuevo evento',
			'date_event': '2026-10-10',
			'user': self.other_owner.id,
		}, format='json')

		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.assertEqual(response.data['user'], self.owner.id)
		self.assertEqual(Activity.objects.get(pk=response.data['id']).user, self.owner)

	def test_subtask_create_assigns_owner_and_rejects_foreign_activity(self):
		today = timezone.localdate()
		due_date = today + timedelta(days=1)
		self.own_activity.date_event = today + timedelta(days=2)
		self.own_activity.save(update_fields=['date_event'])

		own_response = self.client.post(reverse('subtask-list'), {
			'activity': self.own_activity.id,
			'name': 'Confirmar catering',
			'due_date': due_date.isoformat(),
			'scheduled_date': today.isoformat(),
			'estimated_hours': '2.0',
			'user': self.other_owner.id,
		}, format='json')
		foreign_response = self.client.post(reverse('subtask-list'), {
			'activity': self.other_activity.id,
			'name': 'Invadir evento ajeno',
			'due_date': '2026-10-04',
			'scheduled_date': '2026-10-03',
			'estimated_hours': '2.0',
		}, format='json')

		self.assertEqual(own_response.status_code, status.HTTP_201_CREATED)
		self.assertEqual(own_response.data['user'], self.owner.id)
		self.assertEqual(foreign_response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(Subtask.objects.count(), 1)

	def test_subtask_create_does_not_trust_client_today_for_past_due_date(self):
		today = timezone.localdate()
		self.own_activity.date_event = today + timedelta(days=2)
		self.own_activity.save(update_fields=['date_event'])
		payload = {
			'activity': self.own_activity.id,
			'name': 'Gestión vencida',
			'due_date': (today - timedelta(days=1)).isoformat(),
			'scheduled_date': today.isoformat(),
			'estimated_hours': '1.0',
		}
		future_client_date = today + timedelta(days=30)

		response = self.client.post(
			reverse('subtask-list') + f'?today={future_client_date.isoformat()}',
			payload,
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn('due_date', response.data)
		self.assertEqual(Subtask.objects.count(), 0)

	def test_activity_date_cannot_precede_existing_subtask_dates(self):
		today = timezone.localdate()
		self.own_activity.date_event = today + timedelta(days=10)
		self.own_activity.save(update_fields=['date_event'])
		subtask_due_date = today + timedelta(days=5)
		Subtask.objects.create(
			user=self.owner,
			activity=self.own_activity,
			name='Gestión vinculada',
			due_date=subtask_due_date,
			scheduled_date=today + timedelta(days=4),
			estimated_hours=Decimal('1.0'),
		)

		response = self.client.patch(
			reverse('activity-detail', args=[self.own_activity.id]),
			{'date_event': (today + timedelta(days=3)).isoformat()},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn('date_event', response.data)
		self.own_activity.refresh_from_db()
		self.assertEqual(self.own_activity.date_event, today + timedelta(days=10))

	def test_activity_date_cannot_precede_scheduled_subtask_date(self):
		today = timezone.localdate()
		self.own_activity.date_event = today + timedelta(days=10)
		self.own_activity.save(update_fields=['date_event'])
		Subtask.objects.create(
			user=self.owner,
			activity=self.own_activity,
			name='Gestión programada',
			due_date=today + timedelta(days=2),
			scheduled_date=today + timedelta(days=5),
			estimated_hours=Decimal('1.0'),
		)

		response = self.client.patch(
			reverse('activity-detail', args=[self.own_activity.id]),
			{'date_event': (today + timedelta(days=4)).isoformat()},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn('date_event', response.data)

	def test_foreign_subtasks_do_not_leak_in_activity_details(self):
		Subtask.objects.create(
			user=self.other_owner,
			activity=self.own_activity,
			name='Inconsistent legacy relation',
			due_date=date(2026, 10, 4),
			scheduled_date=date(2026, 10, 3),
			estimated_hours=Decimal('1.0'),
		)

		response = self.client.get(reverse('activity-detail', args=[self.own_activity.id]))

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['subtasks'], [])
		self.assertEqual(response.data['total_subtasks'], 0)

	def test_foreign_subtask_cannot_be_retrieved_updated_or_deleted(self):
		foreign_subtask = Subtask.objects.create(
			user=self.other_owner,
			activity=self.other_activity,
			name='Subtarea ajena',
			due_date=date(2026, 10, 4),
			scheduled_date=date(2026, 10, 3),
			estimated_hours=Decimal('1.0'),
		)
		detail_url = reverse('subtask-detail', args=[foreign_subtask.id])

		get_response = self.client.get(detail_url)
		update_response = self.client.patch(detail_url, {'name': 'Intento de edición'}, format='json')
		delete_response = self.client.delete(detail_url)

		self.assertEqual(get_response.status_code, status.HTTP_404_NOT_FOUND)
		self.assertEqual(update_response.status_code, status.HTTP_404_NOT_FOUND)
		self.assertEqual(delete_response.status_code, status.HTTP_404_NOT_FOUND)
		self.assertTrue(Subtask.objects.filter(pk=foreign_subtask.id).exists())

	def test_daily_capacity_is_read_only_and_list_is_owner_scoped(self):
		today = timezone.localdate()
		own_capacity = DailyCapacity.objects.create(
			user=self.owner,
			date=today,
			total_hours_assigned=Decimal('3.0'),
		)
		DailyCapacity.objects.create(
			user=self.other_owner,
			date=today,
			total_hours_assigned=Decimal('4.0'),
		)

		create_response = self.client.post(reverse('daily-capacity-list'), {
			'date': today.isoformat(),
			'total_hours_assigned': '5.0',
		}, format='json')
		list_response = self.client.get(reverse('daily-capacity-list'))

		self.assertEqual(create_response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
		self.assertEqual(list_response.status_code, status.HTTP_200_OK)
		self.assertEqual([item['id'] for item in list_response.data], [own_capacity.id])

	def test_capacity_summary_ignores_client_supplied_user_id(self):
		Subtask.objects.create(
			user=self.owner,
			activity=self.own_activity,
			name='Mi tarea',
			due_date=date(2026, 10, 4),
			scheduled_date=date(2026, 10, 3),
			estimated_hours=Decimal('2.0'),
		)
		Subtask.objects.create(
			user=self.other_owner,
			activity=self.other_activity,
			name='Tarea ajena',
			due_date=date(2026, 10, 4),
			scheduled_date=date(2026, 10, 3),
			estimated_hours=Decimal('4.0'),
		)
		Subtask.objects.create(
			user=self.other_owner,
			activity=self.own_activity,
			name='Relación inconsistente',
			due_date=date(2026, 10, 4),
			scheduled_date=date(2026, 10, 3),
			estimated_hours=Decimal('5.0'),
		)

		# El resumen calcula la carga por due_date (fecha límite).
		response = self.client.get(reverse('daily-capacity-resumen'), {
			'date': '2026-10-04',
			'user_id': self.other_owner.id,
		})

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['assigned_hours'], 2.0)
