"""Page routes. One page: the operator screen at a station."""

from flask import Blueprint, abort, render_template

from config import config
from . import db

bp = Blueprint("pages", __name__)


@bp.route("/")
def index():
    """Station picker. Only useful during setup, not on a locked down tablet."""
    return render_template("index.html", stations=config.STATIONS)


@bp.route("/station/<station_key>")
def station(station_key):
    """
    The operator screen.

    The tablet is bound to its station by this URL, set as the kiosk browser
    home page. That is the simplest of the three options in Q1 and needs no
    device identifier, no login and no setup screen.
    """
    st = config.STATIONS.get(station_key)
    if not st:
        abort(404)

    status = db.get_station_status(st["code"])

    return render_template(
        "station.html",
        station=st,
        station_key=station_key,
        status=status,
        refresh_ms=config.REFRESH_MS,
        demo=config.demo_mode,
    )


@bp.route("/healthz")
def healthz():
    return db.healthcheck()
