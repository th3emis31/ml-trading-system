#!/usr/bin/env python3
"""
JARVIS Voice Pause/Resume API Routes
- Provides endpoints to control voice pause/resume
- Integrates with voice pause manager
- NO DELETIONS - Safe additive routes
"""

from flask import Blueprint, jsonify, request
import logging
from jarvis_voice_pause_manager import get_pause_manager, init_pause_manager

logger = logging.getLogger("jarvis.voice_pause_routes")

# Create blueprint
pause_voice_bp = Blueprint('pause_voice', __name__, url_prefix='/api/jarvis/voice-pause')

def register_pause_voice_routes(app):
    """Register voice pause routes to Flask app"""
    app.register_blueprint(pause_voice_bp)
    logger.info("Voice pause routes registered")


@pause_voice_bp.route('/status', methods=['GET'])
def get_pause_status():
    """Get current pause status"""
    try:
        manager = get_pause_manager()
        status = manager.get_pause_status()
        
        return jsonify({
            'success': True,
            'status': status,
            'message': 'Pause status retrieved'
        }), 200
    except Exception as e:
        logger.error(f"Error getting pause status: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@pause_voice_bp.route('/pause', methods=['POST'])
def request_voice_pause():
    """Request voice listening pause"""
    try:
        manager = get_pause_manager()
        reason = request.get_json() or {}
        reason_text = reason.get('reason', 'Manual pause request')
        
        success = manager.request_pause(reason_text)
        
        return jsonify({
            'success': success,
            'message': 'Voice pause requested',
            'status': manager.get_pause_status()
        }), 200 if success else 400
    except Exception as e:
        logger.error(f"Error pausing voice: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@pause_voice_bp.route('/resume', methods=['POST'])
def request_voice_resume():
    """Request voice listening resume"""
    try:
        manager = get_pause_manager()
        reason = request.get_json() or {}
        reason_text = reason.get('reason', 'Manual resume request')
        
        success = manager.request_resume(reason_text)
        
        return jsonify({
            'success': success,
            'message': 'Voice resume requested',
            'status': manager.get_pause_status()
        }), 200 if success else 400
    except Exception as e:
        logger.error(f"Error resuming voice: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@pause_voice_bp.route('/speaking-started', methods=['POST'])
def notify_speaking_started():
    """Notify that JARVIS started speaking"""
    try:
        manager = get_pause_manager()
        data = request.get_json() or {}
        text = data.get('text', '')
        
        manager.speaking_started(text)
        
        return jsonify({
            'success': True,
            'message': 'Speaking started notification received',
            'status': manager.get_pause_status()
        }), 200
    except Exception as e:
        logger.error(f"Error notifying speaking start: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@pause_voice_bp.route('/speaking-ended', methods=['POST'])
def notify_speaking_ended():
    """Notify that JARVIS finished speaking"""
    try:
        manager = get_pause_manager()
        data = request.get_json() or {}
        auto_resume = data.get('auto_resume', True)
        
        manager.speaking_ended(auto_resume=auto_resume)
        
        return jsonify({
            'success': True,
            'message': 'Speaking ended notification received',
            'auto_resume': auto_resume,
            'status': manager.get_pause_status()
        }), 200
    except Exception as e:
        logger.error(f"Error notifying speaking end: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@pause_voice_bp.route('/force-resume', methods=['POST'])
def force_voice_resume():
    """Force resume voice listening immediately"""
    try:
        manager = get_pause_manager()
        success = manager.force_resume()
        
        return jsonify({
            'success': success,
            'message': 'Force resume attempted' if success else 'Not paused',
            'status': manager.get_pause_status()
        }), 200 if success else 400
    except Exception as e:
        logger.error(f"Error force resuming: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@pause_voice_bp.route('/reset-stats', methods=['POST'])
def reset_pause_stats():
    """Reset pause/speak statistics"""
    try:
        manager = get_pause_manager()
        manager.reset_stats()
        
        return jsonify({
            'success': True,
            'message': 'Pause statistics reset',
            'status': manager.get_pause_status()
        }), 200
    except Exception as e:
        logger.error(f"Error resetting stats: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@pause_voice_bp.route('/health', methods=['GET'])
def pause_voice_health():
    """Check voice pause system health"""
    try:
        manager = get_pause_manager()
        status = manager.get_pause_status()
        
        # System is healthy if we can get status
        health = {
            'system': 'operational',
            'is_paused': status['is_paused'],
            'is_speaking': status['is_speaking'],
            'pause_count': status['pause_count'],
            'speak_count': status['speak_count']
        }
        
        return jsonify({
            'success': True,
            'health': health
        }), 200
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'health': {'system': 'error'}
        }), 500
