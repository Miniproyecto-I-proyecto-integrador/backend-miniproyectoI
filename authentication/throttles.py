from rest_framework.throttling import SimpleRateThrottle


class LoginRateThrottle(SimpleRateThrottle):
    """Limita intentos de inicio de sesión por IP para reducir fuerza bruta."""

    scope = 'login'

    def get_cache_key(self, request, view):
        if request.method != 'POST':
            return None
        ident = self.get_ident(request)
        return self.cache_format % {
            'scope': self.scope,
            'ident': ident,
        }
