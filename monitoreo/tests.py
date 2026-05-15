"""Automated tests for monitoring models and business services."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import Alerta, Estanque, LecturaSensor, Sensor, TipoSensor
from .services import (
    obtener_datos_dashboard,
    obtener_o_crear_sensor,
    procesar_datos_sensor,
    validar_datos_sensor,
)


class ModelosMonitoreoTests(TestCase):
    """Test the behavior of domain models used by the monitoring MVP.

    These tests validate instance creation, useful string representations,
    relationships and model methods without involving views or MQTT.
    """

    def setUp(self):
        """Create reusable sensor data for model tests.

        Parameters:
            None.

        Returns:
            None.

        Raises:
            None.
        """
        self.tipo_sensor = TipoSensor.objects.create(
            nombre="Nivel",
            unidad_medida="%",
        )
        self.sensor = Sensor.objects.create(
            tipo=self.tipo_sensor,
            identificador="sensor-nivel-01",
            nombre="Sensor nivel estanque",
            ubicacion="Estanque principal",
        )

    def test_creacion_sensor_y_str(self):
        """Verifica creacion de sensor y texto descriptivo.

        Entrada: TipoSensor y Sensor validos.
        Resultado esperado: el sensor conserva su relacion y __str__ es util.
        """
        self.assertEqual(self.sensor.tipo, self.tipo_sensor)
        self.assertIn("sensor-nivel-01", str(self.sensor))
        self.assertIn("Nivel", str(self.sensor))

    def test_obtener_ultima_lectura(self):
        """Verifica que el sensor devuelva su lectura mas reciente.

        Entrada: sensor con una lectura registrada.
        Resultado esperado: obtener_ultima_lectura retorna esa lectura.
        """
        lectura = LecturaSensor.objects.create(
            sensor=self.sensor,
            nivel_agua=Decimal("75.00"),
            bidones_producidos=10,
            datos_originales={"nivel": 75, "bidones": 10},
        )

        self.assertEqual(self.sensor.obtener_ultima_lectura(), lectura)

    def test_lectura_rechaza_nivel_fuera_de_rango(self):
        """Verifica validacion de nivel de agua fuera del rango permitido.

        Entrada: lectura con nivel 101.
        Resultado esperado: full_clean levanta ValidationError.
        """
        lectura = LecturaSensor(
            sensor=self.sensor,
            nivel_agua=Decimal("101.00"),
            bidones_producidos=1,
        )

        with self.assertRaises(ValidationError):
            lectura.full_clean()

    def test_estanque_actualiza_desde_lectura(self):
        """Verifica actualizacion del nivel del estanque desde una lectura.

        Entrada: estanque y lectura valida con nivel 60.
        Resultado esperado: el estanque guarda nivel_actual 60.
        """
        estanque = Estanque.objects.create(
            nombre="Estanque principal",
            sensor_nivel=self.sensor,
            nivel_actual=Decimal("0.00"),
        )
        lectura = LecturaSensor.objects.create(
            sensor=self.sensor,
            nivel_agua=Decimal("60.00"),
            bidones_producidos=5,
        )

        estanque.actualizar_desde_lectura(lectura)
        estanque.refresh_from_db()

        self.assertEqual(estanque.nivel_actual, Decimal("60.00"))

    def test_alerta_resolver_cambia_estado(self):
        """Verifica que una alerta activa pueda marcarse como resuelta.

        Entrada: alerta activa.
        Resultado esperado: estado RESUELTA y fecha de resolucion registrada.
        """
        alerta = Alerta.objects.create(
            mensaje="Nivel bajo de agua",
            severidad=Alerta.Severidad.ADVERTENCIA,
        )

        alerta.resolver()
        alerta.refresh_from_db()

        self.assertEqual(alerta.estado, Alerta.Estado.RESUELTA)
        self.assertIsNotNone(alerta.resuelta_en)


class ServiciosMonitoreoTests(TestCase):
    """Test the business services that process IoT monitoring data.

    These tests validate payload validation, controlled failures, reading
    creation, alert generation and dashboard aggregation.
    """

    def test_validar_datos_sensor_validos(self):
        """Verifica normalizacion de un payload IoT valido.

        Entrada: {"nivel": 75, "bidones": 10}.
        Resultado esperado: nivel Decimal, bidones entero y pH opcional nulo.
        """
        datos = validar_datos_sensor({"nivel": 75, "bidones": 10})

        self.assertEqual(datos["nivel"], Decimal("75"))
        self.assertEqual(datos["bidones"], 10)
        self.assertIsNone(datos["ph"])

    def test_validar_datos_sensor_rechaza_bidones_negativos(self):
        """Verifica rechazo de cantidad negativa de bidones.

        Entrada: {"nivel": 75, "bidones": -1}.
        Resultado esperado: ValidationError y ningun guardado en BD.
        """
        with self.assertRaises(ValidationError):
            validar_datos_sensor({"nivel": 75, "bidones": -1})

        self.assertEqual(LecturaSensor.objects.count(), 0)

    def test_validar_datos_sensor_rechaza_json_invalido(self):
        """Verifica rechazo de estructura JSON no compatible.

        Entrada: lista en vez de objeto JSON.
        Resultado esperado: ValidationError controlado.
        """
        with self.assertRaises(ValidationError):
            validar_datos_sensor(["nivel", 75])

    def test_obtener_o_crear_sensor_reutiliza_sensor_existente(self):
        """Verifica que el servicio no duplique sensores existentes.

        Entrada: mismo tipo e identificador en dos llamadas.
        Resultado esperado: se retorna el mismo sensor.
        """
        primero = obtener_o_crear_sensor("ESP32", "esp32-test")
        segundo = obtener_o_crear_sensor("ESP32", "esp32-test")

        self.assertEqual(primero.id, segundo.id)
        self.assertEqual(Sensor.objects.count(), 1)

    def test_procesar_datos_sensor_validos_crea_lectura_y_estanque(self):
        """Verifica flujo de servicio para un mensaje IoT valido.

        Entrada: {"nivel": 75, "bidones": 10}.
        Resultado esperado: lectura guardada, estanque actualizado y sin alerta.
        """
        resultado = procesar_datos_sensor({"nivel": 75, "bidones": 10})

        self.assertTrue(resultado["ok"])
        self.assertEqual(LecturaSensor.objects.count(), 1)
        self.assertEqual(Estanque.objects.first().nivel_actual, Decimal("75.00"))
        self.assertEqual(Alerta.objects.count(), 0)

    def test_procesar_datos_sensor_nivel_bajo_genera_alerta(self):
        """Verifica generacion de alerta cuando el nivel esta bajo el umbral.

        Entrada: {"nivel": 10, "bidones": 3}.
        Resultado esperado: lectura guardada y una alerta activa.
        """
        resultado = procesar_datos_sensor({"nivel": 10, "bidones": 3})

        self.assertTrue(resultado["ok"])
        self.assertEqual(LecturaSensor.objects.count(), 1)
        self.assertEqual(Alerta.objects.count(), 1)
        self.assertTrue(Alerta.objects.first().esta_activa())

    def test_procesar_datos_sensor_invalido_no_guarda_lectura(self):
        """Verifica rechazo controlado de payload con nivel fuera de rango.

        Entrada: {"nivel": 101, "bidones": 1}.
        Resultado esperado: ok False y cero lecturas guardadas.
        """
        resultado = procesar_datos_sensor({"nivel": 101, "bidones": 1})

        self.assertFalse(resultado["ok"])
        self.assertEqual(LecturaSensor.objects.count(), 0)

    def test_obtener_datos_dashboard_resume_estado(self):
        """Verifica datos agregados para el dashboard.

        Entrada: una lectura valida procesada por services.
        Resultado esperado: nivel, bidones y estado general coherentes.
        """
        procesar_datos_sensor({"nivel": 70, "bidones": 12})

        datos = obtener_datos_dashboard()

        self.assertEqual(datos["nivel_actual"], 70.0)
        self.assertEqual(datos["total_bidones"], 12)
        self.assertEqual(datos["alertas_activas"], 0)
        self.assertEqual(datos["estado_general"], "Operativo")
