"""Instância única de templates Jinja (globals de SEO, recaptcha, analytics)."""
from fastapi.templating import Jinja2Templates

from app.core import settings

templates = Jinja2Templates(directory="templates")


def aplicar_globals():
    templates.env.globals["recaptcha_site_key"] = settings.recaptcha_site_key()
    templates.env.globals["ga_measurement_id"] = settings.ga_measurement_id()
    templates.env.globals["contato_email"] = settings.contato_email()
    templates.env.globals["site_url"] = settings.site_url()


aplicar_globals()
