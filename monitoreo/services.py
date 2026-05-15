"""Business services for the San Miguel plant monitoring system."""

from decimal import Decimal, InvalidOperation
import logging

from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models import Sum

from .models import Alerta, Estanque, LecturaSensor, Sensor, TipoSensor


logger = logging.getLogger(__name__)

SENSOR_TIPO_ESP32 = "ESP32"
SENSOR_ID_DEFAULT = "esp32-san-miguel"
ESTANQUE_PRINCIPAL = "Estanque principal"
UMBRAL_NIVEL_BAJO = 20


def _es_numero_valido(valor):
    """Check whether a value is a valid JSON numeric value.

    Parameters:
        valor (object): Value received from the sensor payload.

    Returns:
        bool: True when the value is int, float or Decimal, excluding bool.

    Raises:
        None.
    """
    return isinstance(valor, (int, float, Decimal)) and not isinstance(valor, bool)


def _convertir_decimal(valor, campo):
    """Convert a validated numeric value to Decimal.

    Parameters:
        valor (int | float | Decimal): Numeric value to convert.
        campo (str): Field name used in validation errors.

    Returns:
        Decimal: Converted value ready for model storage.

    Raises:
        ValidationError: If the value cannot be converted safely.
    """
    if not _es_numero_valido(valor):
        raise ValidationError({campo: f"{campo} debe ser numerico."})

    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError({campo: f"{campo} no tiene un formato valido."}) from exc


def _validar_entero_no_negativo(valor, campo):
    """Validate that a value is a non-negative integer.

    Parameters:
        valor (object): Value received from the sensor payload.
        campo (str): Field name used in validation errors.

    Returns:
        int: Validated integer value.

    Raises:
        ValidationError: If the value is not an integer or is negative.
    """
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise ValidationError({campo: f"{campo} debe ser un entero."})

    if valor < 0:
        raise ValidationError({campo: f"{campo} debe ser mayor o igual a 0."})

    return valor


def validar_datos_sensor(data):
    """Validate and normalize data received from the IoT sensor flow.

    Parameters:
        data (dict): JSON-decoded payload with nivel, bidones and optional ph.

    Returns:
        dict: Normalized data ready to create a LecturaSensor.

    Raises:
        ValidationError: If required fields are missing or values are invalid.
    """
    if not isinstance(data, dict):
        logger.warning("Payload IoT invalido: se esperaba un objeto JSON.")
        raise ValidationError("El payload debe ser un objeto JSON.")

    campos_obligatorios = {"nivel", "bidones"}
    faltantes = campos_obligatorios - set(data.keys())
    if faltantes:
        logger.warning("Payload IoT incompleto. Faltan campos: %s", sorted(faltantes))
        raise ValidationError(
            {"campos": f"Faltan campos obligatorios: {', '.join(sorted(faltantes))}."}
        )

    nivel = _convertir_decimal(data["nivel"], "nivel")
    if not 0 <= nivel <= 100:
        logger.warning("Nivel fuera de rango recibido: %s", nivel)
        raise ValidationError({"nivel": "El nivel debe estar entre 0 y 100."})

    bidones = _validar_entero_no_negativo(data["bidones"], "bidones")

    ph = None
    if "ph" in data and data["ph"] is not None:
        ph = _convertir_decimal(data["ph"], "ph")
        if not 0 <= ph <= 14:
            logger.warning("pH fuera de rango recibido: %s", ph)
            raise ValidationError({"ph": "El pH debe estar entre 0 y 14."})

    return {
        "nivel": nivel,
        "bidones": bidones,
        "ph": ph,
        "datos_originales": dict(data),
    }


def obtener_o_crear_sensor(
    tipo,
    identificador,
    nombre=None,
    unidad_medida="",
    ubicacion="",
):
    """Get or create a physical sensor and its type.

    Parameters:
        tipo (str): Sensor type name.
        identificador (str): Unique identifier within the sensor type.
        nombre (str | None): Human-readable sensor name.
        unidad_medida (str): Optional measurement unit for the type.
        ubicacion (str): Optional physical location.

    Returns:
        Sensor: Existing or newly created sensor.

    Raises:
        DatabaseError: If the database operation fails.
        ValidationError: If model validation fails.
    """
    with transaction.atomic():
        tipo_sensor, _ = TipoSensor.objects.get_or_create(
            nombre=tipo,
            defaults={"unidad_medida": unidad_medida},
        )
        sensor, creado = Sensor.objects.get_or_create(
            tipo=tipo_sensor,
            identificador=identificador,
            defaults={
                "nombre": nombre or f"Sensor {identificador}",
                "ubicacion": ubicacion,
            },
        )

        if creado:
            sensor.full_clean()
            sensor.save()
            logger.info("Sensor creado: %s", sensor)

        return sensor


def crear_lectura(sensor, datos):
    """Create a sensor reading using already validated data.

    Parameters:
        sensor (Sensor): Sensor that produced the reading.
        datos (dict): Normalized data returned by validar_datos_sensor.

    Returns:
        LecturaSensor: Persisted reading.

    Raises:
        DatabaseError: If the database operation fails.
        ValidationError: If model validation fails.
    """
    lectura = LecturaSensor(
        sensor=sensor,
        nivel_agua=datos["nivel"],
        bidones_producidos=datos["bidones"],
        ph=datos.get("ph"),
        datos_originales=datos.get("datos_originales", {}),
    )
    lectura.full_clean()
    lectura.save()
    logger.info("Lectura creada: %s", lectura)
    return lectura


def obtener_o_crear_estanque(sensor):
    """Get or create the main monitored tank.

    Parameters:
        sensor (Sensor): Sensor associated with the tank water level.

    Returns:
        Estanque: Main tank used by the dashboard.

    Raises:
        DatabaseError: If the database operation fails.
        ValidationError: If model validation fails.
    """
    estanque, creado = Estanque.objects.get_or_create(
        nombre=ESTANQUE_PRINCIPAL,
        defaults={"sensor_nivel": sensor},
    )

    if creado:
        estanque.full_clean()
        estanque.save()
        logger.info("Estanque principal creado.")
    elif estanque.sensor_nivel_id is None:
        estanque.sensor_nivel = sensor
        estanque.full_clean()
        estanque.save(update_fields=["sensor_nivel", "actualizado_en"])

    return estanque


def _resolver_alertas_nivel_normal(estanque):
    """Resolve active low-level alerts when the tank returns to a safe state.

    Parameters:
        estanque (Estanque): Tank whose active alerts should be reviewed.

    Returns:
        int: Number of alerts resolved.

    Raises:
        DatabaseError: If the update operation fails.
    """
    alertas = Alerta.objects.filter(
        estanque=estanque,
        estado=Alerta.Estado.ACTIVA,
        mensaje__icontains="Nivel bajo",
    )
    cantidad = 0
    for alerta in alertas:
        alerta.resolver()
        cantidad += 1
    return cantidad


def evaluar_alertas(lectura):
    """Evaluate whether a reading should create or resolve alerts.

    Parameters:
        lectura (LecturaSensor): Persisted reading to evaluate.

    Returns:
        list[Alerta]: Alerts created or kept active for the reading.

    Raises:
        DatabaseError: If alert queries or writes fail.
        ValidationError: If an alert cannot be validated.
    """
    estanque = obtener_o_crear_estanque(lectura.sensor)
    alertas = []

    if lectura.nivel_bajo(UMBRAL_NIVEL_BAJO):
        alerta = Alerta.objects.filter(
            estanque=estanque,
            estado=Alerta.Estado.ACTIVA,
            mensaje__icontains="Nivel bajo",
        ).first()

        if alerta is None:
            alerta = Alerta(
                estanque=estanque,
                lectura=lectura,
                mensaje=f"Nivel bajo de agua: {lectura.nivel_agua}%",
                severidad=Alerta.Severidad.ADVERTENCIA,
            )
            alerta.full_clean()
            alerta.save()
            logger.warning("Alerta creada por nivel bajo: %s", alerta)

        alertas.append(alerta)
    else:
        resueltas = _resolver_alertas_nivel_normal(estanque)
        if resueltas:
            logger.info("Alertas de nivel bajo resueltas: %s", resueltas)

    return alertas


def procesar_datos_sensor(data):
    """Validate, persist and evaluate a complete IoT payload.

    Parameters:
        data (dict): JSON-decoded MQTT payload.

    Returns:
        dict: Controlled result with ok, lectura and alertas or error message.

    Raises:
        None. Validation and database errors are logged and returned safely.
    """
    logger.info("Procesando payload IoT: %s", data)

    try:
        datos = validar_datos_sensor(data)
        identificador = data.get("sensor_id", SENSOR_ID_DEFAULT)

        with transaction.atomic():
            sensor = obtener_o_crear_sensor(
                tipo=SENSOR_TIPO_ESP32,
                identificador=identificador,
                nombre="ESP32 Planta San Miguel",
                ubicacion="Planta Purificadora San Miguel",
            )
            lectura = crear_lectura(sensor, datos)
            estanque = obtener_o_crear_estanque(sensor)
            estanque.actualizar_desde_lectura(lectura)
            alertas = evaluar_alertas(lectura)

        return {"ok": True, "lectura": lectura, "alertas": alertas}

    except ValidationError as exc:
        logger.warning("Payload IoT rechazado por validacion: %s", exc)
        return {"ok": False, "error": exc.messages if hasattr(exc, "messages") else str(exc)}
    except DatabaseError as exc:
        logger.error("Error de base de datos procesando payload IoT: %s", exc)
        return {"ok": False, "error": "Error de base de datos al procesar datos IoT."}


def obtener_datos_dashboard():
    """Build the data required by the dashboard view.

    Parameters:
        None.

    Returns:
        dict: Aggregated operational data for the dashboard template.

    Raises:
        None. Database errors are logged and returned as safe empty values.
    """
    try:
        estanque = Estanque.objects.order_by("nombre").first()
        ultima_lectura = LecturaSensor.objects.select_related("sensor").first()
        total_bidones = (
            LecturaSensor.objects.aggregate(total=Sum("bidones_producidos"))["total"]
            or 0
        )
        alertas_activas = Alerta.objects.filter(estado=Alerta.Estado.ACTIVA).count()

        if estanque is None and ultima_lectura is None:
            estado_general = "Sin datos"
        elif alertas_activas:
            estado_general = "Con alertas"
        else:
            estado_general = "Operativo"

        return {
            "estanque": estanque,
            "nivel_actual": estanque.calcular_porcentaje_nivel() if estanque else None,
            "ultima_lectura": ultima_lectura,
            "total_bidones": total_bidones,
            "alertas_activas": alertas_activas,
            "estado_general": estado_general,
        }
    except DatabaseError as exc:
        logger.error("Error obteniendo datos del dashboard: %s", exc)
        return {
            "estanque": None,
            "nivel_actual": None,
            "ultima_lectura": None,
            "total_bidones": 0,
            "alertas_activas": 0,
            "estado_general": "Error de datos",
        }
