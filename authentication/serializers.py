from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError, transaction
from rest_framework import serializers
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'first_name', 'last_name')
        read_only_fields = fields


class RegisterSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(required=True, allow_blank=False)
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'first_name', 'last_name', 'password')
        read_only_fields = ('id',)
        extra_kwargs = {
            'first_name': {'required': True, 'allow_blank': False},
            'last_name': {'required': True, 'allow_blank': False},
        }

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError('Este nombre de usuario ya está registrado.')
        return value

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('Este correo ya está registrado.')
        return value

    def validate_password(self, value):
        validate_password(value)
        return value

    def create(self, validated_data):
        validated_data['password'] = make_password(validated_data['password'])
        try:
            with transaction.atomic():
                return User.objects.create(**validated_data)
        except IntegrityError:
            if User.objects.filter(email__iexact=validated_data['email']).exists():
                raise serializers.ValidationError({
                    'email': 'Este correo ya está registrado.'
                })
            if User.objects.filter(username__iexact=validated_data['username']).exists():
                raise serializers.ValidationError({
                    'username': 'Este nombre de usuario ya está registrado.'
                })
            raise


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        email = attrs['email'].strip()
        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            user = None
        except User.MultipleObjectsReturned:
            user = None

        if not user or not user.check_password(attrs['password']) or not user.is_active:
            raise serializers.ValidationError('Correo o contraseña incorrectos.')

        attrs['user'] = user
        return attrs


class RefreshSerializer(serializers.Serializer):
    refresh_token = serializers.CharField()

    def validate(self, attrs):
        token_serializer = TokenRefreshSerializer(
            data={'refresh': attrs['refresh_token']},
            context=self.context,
        )
        try:
            token_serializer.is_valid(raise_exception=True)
        except TokenError as exc:
            # Token inválido, vencido o en lista negra: 401 en vez de 500.
            raise InvalidToken(exc.args[0])
        tokens = token_serializer.validated_data
        return {
            'access_token': tokens['access'],
            'refresh_token': tokens.get('refresh', attrs['refresh_token']),
        }
