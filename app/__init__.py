import os
from flask import Flask
from datetime import timedelta
from dotenv import load_dotenv

def create_app():
    load_dotenv()

    base = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    app = Flask(
        __name__,
        template_folder=os.path.join(base, 'templates'),
        static_folder=os.path.join(base, 'static'),
    )

    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret')
    app.permanent_session_lifetime = timedelta(
        minutes=int(os.getenv('SESSION_LIFETIME_MIN', '120') or 120)
    )

    # Blueprints
    from app.auth import auth_bp
    from app.routes import main
    app.register_blueprint(auth_bp)
    app.register_blueprint(main)

    return app
