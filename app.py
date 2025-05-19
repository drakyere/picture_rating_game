import os
import random
import logging
from pathlib import Path
from datetime import datetime
import webbrowser

from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from prometheus_flask_exporter import PrometheusMetrics
import pandas as pd
import sqlite3  # Kept for local dev fallback

# ================================================
# INITIALIZATION
# ================================================

# Initialize logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment configuration
def load_environment():
    """Load environment variables with fallbacks"""
    try:
        load_dotenv()
        logger.info("Loaded .env file successfully")
    except Exception as e:
        logger.warning(f"Could not load .env file: {str(e)}")
    
    # Set mandatory variables with fallbacks
    os.environ.setdefault('FLASK_ENV', 'development')
    os.environ.setdefault('SECRET_KEY', 'dev-fallback-key')
    os.environ.setdefault('DATABASE_URL', f'sqlite:///{Path(__file__).parent}/data.db')

load_environment()

# ================================================
# APP CONFIGURATION
# ================================================

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.secret_key = os.environ['SECRET_KEY']

# Database configuration
BASE_DIR = Path(__file__).parent
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['DATABASE_URL']
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
metrics = PrometheusMetrics(app)

# ================================================
# DATABASE MODELS
# ================================================

class Feedback(db.Model):
    __tablename__ = 'feedback'
    id = db.Column(db.Integer, primary_key=True)
    age = db.Column(db.Integer, nullable=False)
    gender = db.Column(db.String(20), nullable=False)
    image_id = db.Column(db.String(100), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    timestamp = db.Column(db.DateTime, server_default=db.func.now())

class Stats(db.Model):
    __tablename__ = 'stats'
    id = db.Column(db.Integer, primary_key=True)
    visitors = db.Column(db.Integer, default=0)

# ================================================
# HELPER FUNCTIONS
# ================================================

def init_database():
    """Initialize database tables"""
    with app.app_context():
        db.create_all()
        
        # Initialize visitor counter if it doesn't exist
        if not Stats.query.first():
            db.session.add(Stats(visitors=0))
            db.session.commit()
            logger.info("Initialized visitor counter")

def export_data():
    """Export feedback data to CSV"""
    with app.app_context():
        try:
            df = pd.read_sql_query("SELECT * FROM feedback", db.engine)
            csv_path = os.path.join(BASE_DIR, 'feedback_data.csv')
            df.to_csv(csv_path, index=False)
            logger.info(f"Data exported to {csv_path}")
            
            # Optional browser display
            html = df.to_html()
            temp_html = os.path.join(BASE_DIR, 'temp.html')
            with open(temp_html, 'w') as f:
                f.write(html)
            webbrowser.open(temp_html)
        except Exception as e:
            logger.error(f"Export failed: {str(e)}")

# ================================================
# ROUTES
# ================================================

@app.route('/')
def index():
    logger.info("Accessed landing page")
    return render_template('landing.html')

@app.route('/demographic', methods=['POST'])
def demographic():
    session['age'] = request.form.get('age')
    session['gender'] = request.form.get('gender')
    logger.info(f"Demographic info collected - Age: {session['age']}, Gender: {session['gender']}")
    return redirect(url_for('rating'))

@app.route('/rating')
def rating():
    try:
        static_path = os.path.join(app.static_folder, 'Available_Dataset')
        if not os.path.exists(static_path):
            logger.error(f"Static path not found: {static_path}")
            return render_template('error.html', message="Image directory not found"), 500

        image_files = [f for f in os.listdir(static_path) 
                      if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif'))]
        
        if not image_files:
            logger.error("No images found in directory")
            return render_template('error.html', message="No images available"), 500
            
        selected_images = random.sample(image_files, min(7, len(image_files)))
        logger.info(f"Displaying {len(selected_images)} images for rating")
        return render_template('rating.html', images=selected_images)
        
    except Exception as e:
        logger.error(f"Error in rating route: {str(e)}")
        return render_template('error.html', message="An error occurred"), 500

@app.route('/submit', methods=['POST'])
def submit():
    try:
        logger.info(f"Received submission: {request.form}")
        
        if 'age' not in session or 'gender' not in session:
            logger.warning("Submission attempt without demographic data")
            return redirect(url_for('index'))
            
        ratings = {}
        for key, value in request.form.items():
            if key.startswith('rating_'):
                try:
                    image_id = key.replace('rating_', '')
                    ratings[image_id] = int(value)
                    logger.info(f"Rating for {image_id}: {value}")
                except (ValueError, TypeError):
                    logger.warning(f"Invalid rating value for {key}: {value}")
                    continue

        if not ratings:
            logger.warning("No valid ratings submitted")
            return "No valid ratings submitted", 400

        # Save to database
        for image_id, rating in ratings.items():
            feedback = Feedback(
                age=session['age'],
                gender=session['gender'],
                image_id=image_id,
                rating=rating
            )
            db.session.add(feedback)
        
        db.session.commit()
        logger.info(f"Successfully saved {len(ratings)} ratings to database")
        return redirect(url_for('thankyou', count=len(ratings)))

    except Exception as e:
        db.session.rollback()
        logger.error(f"Submission error: {str(e)}")
        return render_template('error.html', message="Submission failed"), 500

@app.route('/thankyou')
def thankyou():
    count = request.args.get('count', 0)
    logger.info(f"Thank you page shown for {count} ratings")
    return render_template('thankyou.html', feedback_count=count)

@app.route('/view_data')
def view_data():
    feedback = Feedback.query.all()
    return render_template('view_data.html', feedback=feedback)

@app.before_request
def count_visitors():
    if request.path == '/ping':
        return
        
    if 'visited' not in session:
        stats = Stats.query.first()
        if stats:
            stats.visitors += 1
            db.session.commit()
            session['visited'] = True
            logger.info(f"New visitor - Total: {stats.visitors}")

@app.route('/ping')
def ping():
    return "OK", 200

# ================================================
# STARTUP
# ================================================

if __name__ == '__main__':
    # Initialize database before first request
    init_database()
    
    # Get port from environment variable or use default
    port = int(os.environ.get("PORT", 5000))
    
    if os.environ.get('FLASK_ENV') == 'production':
        from waitress import serve
        logger.info(f"Starting production server on 0.0.0.0:{port}")
        serve(
            app,
            host='0.0.0.0',
            port=port,
            threads=8,
            channel_timeout=60
        )
    else:
        logger.info(f"Starting development server on 0.0.0.0:{port}")
        app.run(host='0.0.0.0', port=port, debug=False)
    
    # Export data when server stops (development only)
    if os.environ.get('FLASK_ENV') != 'production':
        export_data()
