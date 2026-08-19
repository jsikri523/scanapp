"""Gunicorn entry point. Matches the pattern used by the other AppX apps."""

from app import create_app

application = create_app()
app = application

if __name__ == "__main__":
    # Local development and floor testing. On the server Gunicorn serves
    # `application`.
    #
    # Debug is OFF by default and must be opted into. This binds to 0.0.0.0 so
    # a tablet on the same Wi-Fi can reach it, and Werkzeug's debugger allows
    # code execution to anyone who can reach the port. Never run it with
    # debug on where a tablet can see it.
    import os
    debug = os.environ.get("SCANAPP_DEBUG") == "1"
    app.run(host="0.0.0.0", port=5005, debug=debug)
