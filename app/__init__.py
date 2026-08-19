"""
scanapp, the SAW 5 scanning application.

Follows the AppX pattern: a Flask app served by Gunicorn over a unix socket,
reverse proxied by nginx at /scanapp/, with its own venv under
/var/www/apps/scanapp/.
"""

from flask import Flask

from config import config


class ScriptNameMiddleware:
    """
    Makes url_for() emit /scanapp/... when we are behind the nginx router.

    nginx proxies /scanapp/ to the socket and strips the prefix, so the app
    sees /station/saw5. Without this the page would link to /station/saw5 at
    the domain root, which does not exist. nginx sends X-Script-Name and we
    put it back.
    """

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        prefix = environ.get("HTTP_X_SCRIPT_NAME", "")
        if prefix:
            environ["SCRIPT_NAME"] = prefix
            path = environ.get("PATH_INFO", "")
            if path.startswith(prefix):
                environ["PATH_INFO"] = path[len(prefix):]
        return self.wsgi_app(environ, start_response)


def create_app():
    app = Flask(__name__)
    app.config.from_object(config)
    app.wsgi_app = ScriptNameMiddleware(app.wsgi_app)

    from .routes import bp as pages
    from .api import bp as api

    app.register_blueprint(pages)
    app.register_blueprint(api, url_prefix="/api")

    return app
