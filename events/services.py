"""Lógica de negocio compartida entre serializers y vistas.

Aquí vivirá también el cálculo de carga diaria (HU-07/HU-08). Por ahora solo
expone el límite diario configurable (HU-12).
"""
from .models import DEFAULT_DAILY_HOURS_LIMIT, OrganizerSettings


def get_daily_limit(user):
    """Límite diario de horas de gestión del organizador.

    Devuelve el valor guardado o, si nunca lo configuró, el valor por defecto
    (6 h). No crea filas: una lectura no debe escribir en la base de datos.
    """
    saved = (
        OrganizerSettings.objects
        .filter(user=user)
        .values_list('daily_hours_limit', flat=True)
        .first()
    )
    return saved if saved is not None else DEFAULT_DAILY_HOURS_LIMIT


def set_daily_limit(user, hours):
    """Guarda (crea o actualiza) el límite diario del organizador."""
    obj, _ = OrganizerSettings.objects.update_or_create(
        user=user,
        defaults={'daily_hours_limit': hours},
    )
    return obj.daily_hours_limit
