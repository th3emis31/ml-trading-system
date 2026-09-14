"""
JARVIS Market Scheduler API Routes - Phase B
Endpoints for controlling market analysis scheduling
"""

from flask import Blueprint, jsonify, request
from jarvis_market_scheduler import get_market_scheduler
import logging

logger = logging.getLogger(__name__)

# Create blueprint
market_scheduler_bp = Blueprint('market_scheduler', __name__, url_prefix='/api/jarvis/market-scheduler')


@market_scheduler_bp.route('/start', methods=['POST'])
def start_scheduler():
    """Start the market scheduler"""
    try:
        scheduler = get_market_scheduler()
        result = scheduler.start()
        
        if result:
            return jsonify({
                'status': 'success',
                'message': 'Market scheduler started',
                'scheduler_running': True,
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to start scheduler',
                'scheduler_running': False,
            }), 500
    
    except Exception as e:
        logger.error(f"Start scheduler error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e),
        }), 500


@market_scheduler_bp.route('/stop', methods=['POST'])
def stop_scheduler():
    """Stop the market scheduler"""
    try:
        scheduler = get_market_scheduler()
        result = scheduler.stop()
        
        if result:
            return jsonify({
                'status': 'success',
                'message': 'Market scheduler stopped',
                'scheduler_running': False,
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to stop scheduler',
                'scheduler_running': True,
            }), 500
    
    except Exception as e:
        logger.error(f"Stop scheduler error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e),
        }), 500


@market_scheduler_bp.route('/status', methods=['GET'])
def get_status():
    """Get market scheduler status"""
    try:
        scheduler = get_market_scheduler()
        status = scheduler.get_status()
        
        return jsonify({
            'status': 'success',
            'scheduler': status,
        })
    
    except Exception as e:
        logger.error(f"Get status error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e),
        }), 500


@market_scheduler_bp.route('/execute', methods=['POST'])
def force_execution():
    """Force immediate market analysis"""
    try:
        scheduler = get_market_scheduler()
        analysis = scheduler.force_execution()
        
        if analysis:
            return jsonify({
                'status': 'success',
                'message': 'Market analysis executed',
                'analysis': analysis,
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Analysis failed',
            }), 500
    
    except Exception as e:
        logger.error(f"Force execution error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e),
        }), 500


@market_scheduler_bp.route('/history', methods=['GET'])
def get_history():
    """Get analysis history"""
    try:
        scheduler = get_market_scheduler()
        history = scheduler.get_analysis_history()
        
        return jsonify({
            'status': 'success',
            'history': history,
            'count': len(history),
        })
    
    except Exception as e:
        logger.error(f"Get history error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e),
        }), 500


@market_scheduler_bp.route('/interval', methods=['POST'])
def set_interval():
    """Set scheduler interval"""
    try:
        data = request.get_json() or {}
        hours = data.get('hours', 2)
        
        scheduler = get_market_scheduler()
        result = scheduler.set_interval(hours)
        
        if result:
            return jsonify({
                'status': 'success',
                'message': f'Scheduler interval set to {hours} hours',
                'interval_hours': hours,
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to set interval',
            }), 500
    
    except ValueError as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
        }), 400
    
    except Exception as e:
        logger.error(f"Set interval error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e),
        }), 500


@market_scheduler_bp.route('/reset-stats', methods=['POST'])
def reset_stats():
    """Reset scheduler statistics"""
    try:
        scheduler = get_market_scheduler()
        scheduler.reset_stats()
        
        return jsonify({
            'status': 'success',
            'message': 'Statistics reset',
        })
    
    except Exception as e:
        logger.error(f"Reset stats error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e),
        }), 500


@market_scheduler_bp.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    try:
        scheduler = get_market_scheduler()
        status = scheduler.get_status()
        
        return jsonify({
            'status': 'ok',
            'service': 'market-scheduler',
            'scheduler_running': status.get('scheduler_active', False),
            'timestamp': __import__('datetime').datetime.now(
                __import__('datetime').timezone.utc
            ).isoformat(),
        })
    
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
        }), 500


def register_market_scheduler_routes(app):
    """Register market scheduler routes with Flask app"""
    app.register_blueprint(market_scheduler_bp)
    logger.info("Market scheduler routes registered")
