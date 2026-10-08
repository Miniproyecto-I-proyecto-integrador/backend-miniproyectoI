"""Lógica de negocio compartida entre serializers y vistas.

Reglas acordadas para la carga diaria (HU-07 / HU-08 / HU-12):
- La fecha que cuenta es ``due_date`` (fecha límite de la gestión).
- Solo ocupan tiempo las gestiones ``pending`` e ``in_progress``. Una gestión
  ``done`` ya se ejecutó y una ``postponed`` se sacó del día (posponer es una de
  las formas de resolver un conflicto).
- Solo se consideran las gestiones del propio organizador.
- El límite diario es configurable por organizador (6 h por defecto).
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import DEFAULT_DAILY_HOURS_LIMIT, OrganizerSettings, Subtask

# Estados que cuentan como carga de trabajo del día.
ACTIVE_STATUSES = ('pending', 'in_progress')

# Alternativas que el frontend debe ofrecer ante un conflicto (HU-07 esc. 3).
RESOLUTION_OPTIONS = ('move', 'reduce_hours', 'postpone')


# --------------------------------------------------------------------------
# Límite diario (HU-12)
# --------------------------------------------------------------------------
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


# --------------------------------------------------------------------------
# Carga diaria (HU-07)
# --------------------------------------------------------------------------
def format_hours(value):
    """7.0 -> '7', 7.5 -> '7.5' (sin ceros decimales sobrantes)."""
    return format(Decimal(str(value)).normalize(), 'f')


def _active_subtasks(user):
    """Gestiones del organizador que ocupan tiempo (ver ACTIVE_STATUSES)."""
    return Subtask.objects.filter(
        user=user,
        activity__user=user,
        status__in=ACTIVE_STATUSES,
    )


def get_day_load(user, day, exclude_id=None):
    """Horas de gestión activas del organizador para un día (due_date)."""
    queryset = _active_subtasks(user).filter(due_date=day)
    if exclude_id is not None:
        queryset = queryset.exclude(pk=exclude_id)
    return queryset.aggregate(total=Sum('estimated_hours'))['total'] or Decimal('0')


def check_day_capacity(user, day, proposed_hours, exclude_id=None, limit=None):
    """Evalúa qué pasaría si se agregan ``proposed_hours`` al día ``day``.

    Devuelve siempre un diccionario con las cifras (útil también para
    consultas sin conflicto). Solo considera el día indicado.
    """
    limit = get_daily_limit(user) if limit is None else limit
    current = get_day_load(user, day, exclude_id=exclude_id)
    proposed = Decimal(str(proposed_hours))
    total = current + proposed
    excess = max(total - limit, Decimal('0'))
    return {
        'date': day.isoformat(),
        'current_hours': float(current),
        'proposed_hours': float(proposed),
        'total_hours': float(total),
        'limit_hours': limit,
        'excess_hours': float(excess),
        'exceeds': total > limit,
        'message': (
            f'Quedarías con {format_hours(total)}h de gestión planificadas '
            f'(límite {limit}h).'
        ),
    }


class OverloadConflict(Exception):
    """Se intentó guardar un cambio que deja un día por encima del límite."""

    def __init__(self, info):
        super().__init__(info['message'])
        self.info = info

    def response_data(self):
        # 'conflicto' conserva el formato anterior (lista con el mensaje) para no
        # romper al frontend; 'conflict' trae las cifras con tipos numéricos.
        return {
            'conflicto': [self.info['message']],
            'conflict': {**self.info, 'options': list(RESOLUTION_OPTIONS)},
        }


def find_overloaded_days(user, limit=None, from_date=None):
    """Días (desde ``from_date``, por defecto hoy ) que superan el límite.

    Cada elemento incluye las gestiones del día para que el usuario pueda
    elegir cuál mover, reducir o posponer.
    """
    limit = get_daily_limit(user) if limit is None else limit
    from_date = from_date or timezone.localdate()

    totals = {
        row['due_date']: row['total']
        for row in (
            _active_subtasks(user)
            .filter(due_date__gte=from_date)
            .values('due_date')
            .annotate(total=Sum('estimated_hours'))
            .filter(total__gt=limit)
            .order_by('due_date')
        )
    }
    if not totals:
        return []

    by_day = defaultdict(list)
    subtasks = (
        _active_subtasks(user)
        .filter(due_date__in=list(totals))
        .select_related('activity')
        .order_by('due_date', 'id')
    )
    for subtask in subtasks:
        by_day[subtask.due_date].append({
            'id': subtask.id,
            'name': subtask.name,
            'activity_id': subtask.activity_id,
            'activity_name': subtask.activity.name,
            'estimated_hours': float(subtask.estimated_hours),
            'status': subtask.status,
        })

    return [
        {
            'date': day.isoformat(),
            'total_hours': float(total),
            'limit_hours': limit,
            'excess_hours': float(total - limit),
            'subtasks': by_day[day],
        }
        for day, total in totals.items()
    ]

# --------------------------------------------------------------------------
# Resolución de conflictos (HU-08)
# --------------------------------------------------------------------------
def suggest_available_day(user, subtask, start_date=None, end_date=None):
    """Devuelve el primer día disponible para reprogramar una subtarea.

    La búsqueda se realiza en dos etapas:

    1. Desde el día posterior a ``start_date`` (hoy del cliente) hasta la
       fecha límite original de la subtarea.
    2. Si no existe capacidad en ese rango, desde el día posterior a la
       fecha límite original hasta la fecha del evento.

    De esta forma se prioriza adelantar la gestión cuando existe capacidad,
    pero también se permite aplazarla hasta la fecha del evento.

    ``start_date`` permite usar la fecha local enviada por el cliente. Si no
    se envía, se usa la fecha local del servidor como respaldo.
    """
    if subtask.user_id != user.pk or subtask.activity.user_id != user.pk:
        return None

    today = start_date or timezone.localdate()
    event_date = end_date or subtask.activity.date_event
    original_due_date = subtask.due_date

    if today >= event_date:
        return None

    limit = get_daily_limit(user)

    # ------------------------------------------------------------------
    # Primera etapa:
    # desde mañana hasta la fecha límite original de la subtarea.
    # ------------------------------------------------------------------
    first_start = today + timedelta(days=1)
    first_end = min(original_due_date, event_date)

    day = first_start

    while day <= first_end:
        info = check_day_capacity(
            user,
            day,
            subtask.estimated_hours,
            exclude_id=subtask.id,
            limit=limit,
        )

        if not info['exceeds']:
            return info

        day += timedelta(days=1)

    # ------------------------------------------------------------------
    # Segunda etapa:
    # si no hay espacio antes/hasta el due_date original,
    # continuar después del due_date hasta la fecha del evento.
    # ------------------------------------------------------------------
    second_start = max(
        original_due_date + timedelta(days=1),
        first_start,
    )

    day = second_start

    while day <= event_date:
        info = check_day_capacity(
            user,
            day,
            subtask.estimated_hours,
            exclude_id=subtask.id,
            limit=limit,
        )
        if not info['exceeds']:
            return info

        day += timedelta(days=1)

    return None
