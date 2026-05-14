"""Root URL configuration for Sistema de Monitoreo San Miguel."""

from django.contrib import admin
from django.urls import path


urlpatterns = [
    path("admin/", admin.site.urls),
]
