from datetime import timedelta
from decimal import Decimal
import time
import uuid

import jwt
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Activity, Subtask

SUPABASE_URL = 'https://test-project.supabase.co'
TEST_JWT_SECRET = 'test-only-supabase-jwt-secret'


@override_settings(
	SUPABASE_URL=SUPABASE_URL,
	SUPABASE_JWT_SECRET=TEST_JWT_SECRET,
	SUPABASE_JWT_AUDIENCE='authenticated',
)
class TodayEndpointTests(APITestCase):
	def setUp(self):
		self.owner_id = uuid.uuid4()
		self.other_owner_id = uuid.uuid4()
		self.today = timezone.localdate()
		self.activity = Activity.objects.create(
			user_id=self.owner_id,
			name='Curso de logística',
			date_event=self.today + timedelta(days=14),
		)
		self.other_activity = Activity.objects.create(
			user_id=self.other_owner_id,
			name='Curso de logística ajeno',
			date_event=self.today + timedelta(days=14),
		)
		self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.make_token(self.owner_id)}')

	@staticmethod
	def make_token(subject, **claim_overrides):
		issued_at = int(time.time())
		claims = {
			'sub': str(subject),
			'aud': 'authenticated',
			'iss': f'{SUPABASE_URL}/auth/v1',
			'role': 'authenticated',
			'iat': issued_at,
			'exp': issued_at + 300,
		}
		claims.update(claim_overrides)
		return jwt.encode(claims, TEST_JWT_SECRET, algorithm='HS256')

	def create_subtask(self, name, due_date, estimated_hours, activity=None, status='pending'):
		return Subtask.objects.create(
			activity=activity or self.activity,
			name=name,
			due_date=due_date,
			scheduled_date=min(due_date, self.today),
			estimated_hours=Decimal(estimated_hours),
			status=status,
		)

	def test_hoy_groups_by_due_date_and_orders_by_effort_for_ties(self):
		overdue_expensive = self.create_subtask(
			'Vencida más costosa', self.today - timedelta(days=1), '3.0'
		)
		overdue_cheap = self.create_subtask(
			'Vencida más ligera', self.today - timedelta(days=1), '1.0'
		)
		due_today_expensive = self.create_subtask('Hoy más costosa', self.today, '2.0')
		due_today_cheap = self.create_subtask('Hoy más ligera', self.today, '1.0')
		nearest_future = self.create_subtask(
			'Próxima cercana', self.today + timedelta(days=1), '5.0'
		)
		later_future = self.create_subtask(
			'Próxima posterior', self.today + timedelta(days=2), '1.0'
		)
		self.create_subtask(
			'Gestión de otro organizador', self.today, '1.0', activity=self.other_activity
		)

		response = self.client.get(reverse('activity-hoy'))

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['total'], 6)
		groups = response.data['grupos']
		self.assertEqual(
			[task['id'] for task in groups['vencidas']],
			[overdue_cheap.id, overdue_expensive.id],
		)
		self.assertEqual(
			[task['id'] for task in groups['para_hoy']],
			[due_today_cheap.id, due_today_expensive.id],
		)
		self.assertEqual(
			[task['id'] for task in groups['proximas']],
			[nearest_future.id, later_future.id],
		)
		self.assertEqual(groups['para_hoy'][0]['event_name'], 'Curso de logística')

	def test_hoy_filters_by_event_name_and_spanish_status_alias(self):
		matching = self.create_subtask(
			'Pendiente propia', self.today + timedelta(days=1), '1.0'
		)
		self.create_subtask(
			'Completada propia', self.today, '1.0', status='done'
		)
		self.create_subtask(
			'Coincidencia ajena', self.today, '1.0', activity=self.other_activity
		)

		response = self.client.get(reverse('activity-hoy'), {
			'curso': 'logíst',
			'estado': 'pendiente',
		})

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['total'], 1)
		self.assertEqual(response.data['grupos']['proximas'][0]['id'], matching.id)
		self.assertEqual(response.data['filtros'], {
			'curso': 'logíst',
			'estado': 'pendiente',
		})

	def test_hoy_rejects_unknown_status(self):
		response = self.client.get(reverse('activity-hoy'), {'estado': 'inexistente'})

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn('estado', response.data)

	def test_hoy_requires_a_valid_supabase_bearer_token(self):
		self.client.credentials()
		anonymous_response = self.client.get(reverse('activity-hoy'))
		self.client.credentials(HTTP_AUTHORIZATION='Bearer token-invalido')
		invalid_token_response = self.client.get(reverse('activity-hoy'))

		self.assertEqual(anonymous_response.status_code, status.HTTP_401_UNAUTHORIZED)
		self.assertEqual(invalid_token_response.status_code, status.HTTP_401_UNAUTHORIZED)

	def test_hoy_openapi_documents_filters_and_bearer(self):
		from drf_yasg import openapi
		from drf_yasg.generators import OpenAPISchemaGenerator

		schema = OpenAPISchemaGenerator(
			info=openapi.Info(title='API Eventos', default_version='v1')
		).get_schema(request=None, public=True)
		path = next(path for path in schema.paths if path.endswith('/actividades/hoy/'))
		get_operation = dict(schema.paths[path].operations)['get']
		parameter_names = {parameter['name'] for parameter in get_operation.parameters}

		self.assertTrue({'curso', 'estado'}.issubset(parameter_names))
		self.assertEqual(schema.security_definitions['Bearer']['name'], 'Authorization')
		self.assertEqual(schema.security, [{'Bearer': []}])
