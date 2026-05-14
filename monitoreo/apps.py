"""Application configuration for the monitoring domain."""

from django.apps import AppConfig


class MonitoreoConfig(AppConfig):
    """Represent the Django app that contains the plant monitoring domain.

    The app groups models, services, MQTT integration, views and templates
    related to the San Miguel purification plant MVP.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "monitoreo"
    verbose_name = "Monitoreo Planta San Miguel"
