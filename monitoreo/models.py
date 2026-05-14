"""Domain models for the San Miguel purification plant monitoring system."""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class TipoSensor(models.Model):
    """Represent a category of sensor used by the plant.

    This model identifies the kind of physical measurement handled by a sensor,
    such as water level, bottle counter or pH. Its responsibility is to keep
    sensor classification data centralized and reusable.
    """

    nombre = models.CharField(max_length=80, unique=True)
    unidad_medida = models.CharField(max_length=20, blank=True)
    descripcion = models.TextField(blank=True)

    class Meta:
        """Define human-readable names and default ordering for sensor types."""

        verbose_name = "tipo de sensor"
        verbose_name_plural = "tipos de sensor"
        ordering = ["nombre"]

    def __str__(self):
        """Return a clean label for admin screens and lists.

        Parameters:
            None.

        Returns:
            str: Sensor type name with its unit when available.

        Raises:
            None.
        """
        if self.unidad_medida:
            return f"{self.nombre} ({self.unidad_medida})"
        return self.nombre


class Sensor(models.Model):
    """Represent a physical sensor or IoT device installed in the plant.

    A sensor belongs to one sensor type and can produce many readings over
    time. Its responsibility is to identify a real data source from the ESP32
    and expose simple helpers related to its readings.
    """

    tipo = models.ForeignKey(
        TipoSensor,
        on_delete=models.PROTECT,
        related_name="sensores",
    )
    identificador = models.CharField(max_length=100)
    nombre = models.CharField(max_length=120)
    ubicacion = models.CharField(max_length=150, blank=True)
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        """Define uniqueness and ordering rules for plant sensors."""

        verbose_name = "sensor"
        verbose_name_plural = "sensores"
        ordering = ["tipo__nombre", "identificador"]
        constraints = [
            models.UniqueConstraint(
                fields=["tipo", "identificador"],
                name="sensor_tipo_identificador_unico",
            ),
        ]

    def __str__(self):
        """Return a useful sensor label.

        Parameters:
            None.

        Returns:
            str: Sensor name, identifier and type.

        Raises:
            None.
        """
        return f"{self.nombre} - {self.identificador} ({self.tipo.nombre})"

    def obtener_ultima_lectura(self):
        """Get the most recent reading produced by this sensor.

        Parameters:
            None.

        Returns:
            LecturaSensor | None: Latest reading or None when no readings exist.

        Raises:
            None.
        """
        return self.lecturas.order_by("-fecha_hora").first()


class LecturaSensor(models.Model):
    """Represent a validated data sample received from the IoT flow.

    The model stores the values received from MQTT after validation. It keeps
    the MVP fields required by the plant: water level, produced bottles and an
    optional pH value prepared for future use.
    """

    sensor = models.ForeignKey(
        Sensor,
        on_delete=models.PROTECT,
        related_name="lecturas",
    )
    nivel_agua = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    bidones_producidos = models.PositiveIntegerField(default=0)
    ph = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        null=True,
        blank=True,
    )
    datos_originales = models.JSONField(default=dict, blank=True)
    fecha_hora = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Define validation constraints and default ordering for readings."""

        verbose_name = "lectura de sensor"
        verbose_name_plural = "lecturas de sensores"
        ordering = ["-fecha_hora"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(nivel_agua__isnull=True)
                    | (Q(nivel_agua__gte=0) & Q(nivel_agua__lte=100))
                ),
                name="lectura_nivel_agua_entre_0_y_100",
            ),
            models.CheckConstraint(
                condition=Q(ph__isnull=True) | (Q(ph__gte=0) & Q(ph__lte=14)),
                name="lectura_ph_entre_0_y_14",
            ),
        ]

    def __str__(self):
        """Return a compact reading summary.

        Parameters:
            None.

        Returns:
            str: Sensor identifier with water level and bottle count.

        Raises:
            None.
        """
        nivel = "sin nivel" if self.nivel_agua is None else f"{self.nivel_agua}%"
        return (
            f"{self.sensor.identificador}: {nivel}, "
            f"{self.bidones_producidos} bidones"
        )

    def clean(self):
        """Validate model values before saving them through services or admin.

        Parameters:
            None.

        Returns:
            None.

        Raises:
            ValidationError: If water level or pH values are outside safe ranges.
        """
        errors = {}

        if self.nivel_agua is not None and not 0 <= self.nivel_agua <= 100:
            errors["nivel_agua"] = "El nivel de agua debe estar entre 0 y 100."

        if self.ph is not None and not 0 <= self.ph <= 14:
            errors["ph"] = "El pH debe estar entre 0 y 14."

        if errors:
            raise ValidationError(errors)

    def nivel_bajo(self, umbral=20):
        """Determine whether the water level is below the alert threshold.

        Parameters:
            umbral (int | float): Minimum acceptable water level percentage.

        Returns:
            bool: True when the reading contains a level below the threshold.

        Raises:
            None.
        """
        if self.nivel_agua is None:
            return False
        return self.nivel_agua < umbral


class Estanque(models.Model):
    """Represent the monitored water tank of the purification plant.

    The tank stores the current water level and can be linked to the sensor
    responsible for reporting that level. Its responsibility is to expose the
    operational state needed by the dashboard.
    """

    nombre = models.CharField(max_length=120, unique=True)
    capacidad_litros = models.PositiveIntegerField(default=1000)
    sensor_nivel = models.ForeignKey(
        Sensor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="estanques",
    )
    nivel_actual = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        """Define human-readable names and ordering for tanks."""

        verbose_name = "estanque"
        verbose_name_plural = "estanques"
        ordering = ["nombre"]
        constraints = [
            models.CheckConstraint(
                condition=Q(nivel_actual__gte=0) & Q(nivel_actual__lte=100),
                name="estanque_nivel_actual_entre_0_y_100",
            ),
        ]

    def __str__(self):
        """Return the tank name and current level.

        Parameters:
            None.

        Returns:
            str: Tank name with current level percentage.

        Raises:
            None.
        """
        return f"{self.nombre} ({self.nivel_actual}%)"

    def clean(self):
        """Validate the current tank level before saving.

        Parameters:
            None.

        Returns:
            None.

        Raises:
            ValidationError: If the current level is outside the 0-100 range.
        """
        if not 0 <= self.nivel_actual <= 100:
            raise ValidationError(
                {"nivel_actual": "El nivel actual debe estar entre 0 y 100."}
            )

    def calcular_porcentaje_nivel(self):
        """Return the tank water level as a percentage.

        Parameters:
            None.

        Returns:
            float: Current water level percentage.

        Raises:
            None.
        """
        return float(self.nivel_actual)

    def actualizar_desde_lectura(self, lectura):
        """Update the tank level using a sensor reading.

        Parameters:
            lectura (LecturaSensor): Reading that contains the latest water level.

        Returns:
            None.

        Raises:
            ValidationError: If the reading has no water level value.
        """
        if lectura.nivel_agua is None:
            raise ValidationError(
                "La lectura no contiene nivel de agua para actualizar el estanque."
            )

        self.nivel_actual = lectura.nivel_agua
        self.full_clean()
        self.save(update_fields=["nivel_actual", "actualizado_en"])


class Alerta(models.Model):
    """Represent an operational alert generated by a relevant plant event.

    Alerts are created from validated readings when the system detects an
    important condition, such as a low water level. Its responsibility is to
    expose the alert state and severity used by the dashboard.
    """

    class Severidad(models.TextChoices):
        """List the supported severity levels for plant alerts."""

        INFO = "INFO", "Informativa"
        ADVERTENCIA = "ADVERTENCIA", "Advertencia"
        CRITICA = "CRITICA", "Critica"

    class Estado(models.TextChoices):
        """List the supported lifecycle states for plant alerts."""

        ACTIVA = "ACTIVA", "Activa"
        RESUELTA = "RESUELTA", "Resuelta"

    estanque = models.ForeignKey(
        Estanque,
        on_delete=models.CASCADE,
        related_name="alertas",
        null=True,
        blank=True,
    )
    lectura = models.ForeignKey(
        LecturaSensor,
        on_delete=models.CASCADE,
        related_name="alertas",
        null=True,
        blank=True,
    )
    mensaje = models.TextField()
    severidad = models.CharField(
        max_length=20,
        choices=Severidad.choices,
        default=Severidad.ADVERTENCIA,
    )
    estado = models.CharField(
        max_length=20,
        choices=Estado.choices,
        default=Estado.ACTIVA,
    )
    creada_en = models.DateTimeField(auto_now_add=True)
    resuelta_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Define ordering and display names for operational alerts."""

        verbose_name = "alerta"
        verbose_name_plural = "alertas"
        ordering = ["-creada_en"]

    def __str__(self):
        """Return a readable alert summary.

        Parameters:
            None.

        Returns:
            str: Severity, status and shortened message.

        Raises:
            None.
        """
        mensaje = self.mensaje[:60]
        return f"{self.severidad} - {self.estado}: {mensaje}"

    def esta_activa(self):
        """Check whether the alert is still active.

        Parameters:
            None.

        Returns:
            bool: True when the alert status is active.

        Raises:
            None.
        """
        return self.estado == self.Estado.ACTIVA

    def resolver(self):
        """Mark the alert as resolved.

        Parameters:
            None.

        Returns:
            None.

        Raises:
            ValidationError: If the alert cannot be validated after the update.
        """
        self.estado = self.Estado.RESUELTA
        self.resuelta_en = timezone.now()
        self.full_clean()
        self.save(update_fields=["estado", "resuelta_en"])
