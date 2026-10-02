from functools import lru_cache
from uuid import UUID

import jwt
from django.conf import settings
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientError
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed


@lru_cache(maxsize=8)
def get_jwks_client(jwks_url):
    return PyJWKClient(jwks_url)


class SupabaseUser:
    def __init__(self, subject, claims):
        self.id = UUID(subject)
        self.pk = self.id
        self.email = claims.get('email', '')
        self.role = claims.get('role', '')

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    @property
    def is_active(self):
        return True

    def __str__(self):
        return str(self.pk)


class SupabaseJWTAuthentication(BaseAuthentication):
    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts:
            return None
        if parts[0].lower() != b'bearer':
            return None
        if len(parts) != 2:
            raise AuthenticationFailed('El header Authorization debe usar Bearer <token>.')

        try:
            token = parts[1].decode('utf-8')
        except UnicodeDecodeError as error:
            raise AuthenticationFailed('El token no tiene un formato válido.') from error

        claims = self._decode_token(token)
        if claims.get('role') != 'authenticated':
            raise AuthenticationFailed('El token no corresponde a un usuario autenticado.')

        subject = claims.get('sub')
        try:
            user = SupabaseUser(subject, claims)
        except (TypeError, ValueError, AttributeError) as error:
            raise AuthenticationFailed('El token no contiene un UUID de usuario válido.') from error

        return user, claims

    def authenticate_header(self, request):
        return 'Bearer'

    def _decode_token(self, token):
        supabase_url = settings.SUPABASE_URL
        if not supabase_url:
            raise AuthenticationFailed('El backend no tiene configurada la URL de Supabase.')

        issuer = f'{supabase_url}/auth/v1'
        try:
            algorithm = jwt.get_unverified_header(token).get('alg')
            if algorithm == 'HS256':
                signing_key = settings.SUPABASE_JWT_SECRET
                if not signing_key:
                    raise AuthenticationFailed('Falta configurar el secreto JWT legacy de Supabase.')
            elif algorithm in {'ES256', 'RS256'}:
                jwks_url = f'{issuer}/.well-known/jwks.json'
                signing_key = get_jwks_client(jwks_url).get_signing_key_from_jwt(token).key
            else:
                raise AuthenticationFailed('El algoritmo de firma del token no está permitido.')

            claims = jwt.decode(
                token,
                signing_key,
                algorithms=[algorithm],
                audience=settings.SUPABASE_JWT_AUDIENCE,
                issuer=issuer,
                options={'require': ['exp', 'sub']},
            )
        except AuthenticationFailed:
            raise
        except (jwt.PyJWTError, InvalidTokenError, PyJWKClientError, OSError, ValueError) as error:
            raise AuthenticationFailed('El token de Supabase es inválido o expiró.') from error

        return claims