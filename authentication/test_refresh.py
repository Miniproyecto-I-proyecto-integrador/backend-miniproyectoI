from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()


class RefreshEndpointTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user('org', 'org@example.com', 'V3ry-long-Test-P@ssw0rd!')
        self.url = reverse('refresh')

    def test_invalid_token_returns_401_not_500(self):
        response = self.client.post(self.url, {'refresh_token': 'basura'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_access_token_used_as_refresh_returns_401(self):
        access = str(RefreshToken.for_user(self.user).access_token)
        response = self.client.post(self.url, {'refresh_token': access}, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_missing_token_returns_400(self):
        response = self.client.post(self.url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('refresh_token', response.json())

    def test_valid_token_returns_new_access_token(self):
        refresh = str(RefreshToken.for_user(self.user))
        response = self.client.post(self.url, {'refresh_token': refresh}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access_token', response.json())
        self.assertIn('refresh_token', response.json())
