from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()


class AuthenticationTests(APITestCase):
    password = 'V3ry-long-Test-P@ssw0rd!'

    def register(self, **overrides):
        payload = {
            'username': 'organizer_a',
            'email': 'a@example.com',
            'first_name': 'Ana',
            'last_name': 'Organizadora',
            'password': self.password,
        }
        payload.update(overrides)
        return self.client.post(reverse('register'), payload, format='json')

    def test_register_stores_profile_names_and_hashes_password(self):
        response = self.register()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username='organizer_a')
        self.assertEqual(user.first_name, 'Ana')
        self.assertEqual(user.last_name, 'Organizadora')
        self.assertNotEqual(user.password, self.password)
        self.assertTrue(user.check_password(self.password))
        self.assertEqual(response.data['user']['first_name'], 'Ana')
        self.assertEqual(response.data['user']['last_name'], 'Organizadora')

    def test_register_rejects_duplicate_email_and_username(self):
        self.register()

        duplicate_email = self.register(username='organizer_b', email='A@EXAMPLE.COM')
        duplicate_username = self.register(email='b@example.com')

        self.assertEqual(duplicate_email.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', duplicate_email.data)
        self.assertEqual(duplicate_username.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('username', duplicate_username.data)

    def test_register_requires_email_and_names(self):
        response = self.register(email='', first_name='')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', response.data)
        self.assertIn('first_name', response.data)

    def test_login_uses_email_and_returns_tokens_and_user_profile(self):
        self.register()

        response = self.client.post(reverse('login'), {
            'email': 'A@EXAMPLE.COM',
            'password': self.password,
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access_token', response.data)
        self.assertIn('refresh_token', response.data)
        self.assertEqual(response.data['user']['first_name'], 'Ana')
        self.assertEqual(response.data['user']['last_name'], 'Organizadora')
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access_token']}")
        me_response = self.client.get(reverse('me'))
        self.assertEqual(me_response.status_code, status.HTTP_200_OK)
        self.assertEqual(me_response.data['email'], 'a@example.com')

    def test_login_rejects_invalid_password(self):
        self.register()

        response = self.client.post(reverse('login'), {
            'email': 'a@example.com',
            'password': 'wrong-password',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_refresh_returns_new_access_token(self):
        self.register()
        login_response = self.client.post(reverse('login'), {
            'email': 'a@example.com',
            'password': self.password,
        }, format='json')

        response = self.client.post(reverse('refresh'), {
            'refresh_token': login_response.data['refresh_token'],
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access_token', response.data)
        self.assertIn('refresh_token', response.data)

    def test_me_requires_authentication(self):
        response = self.client.get(reverse('me'))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)