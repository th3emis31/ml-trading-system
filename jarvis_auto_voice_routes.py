#!/usr/bin/env python3
"""
JARVIS Auto-Voice API Routes
- Manages auto-listening endpoints
- Provides status and control endpoints
- NO DELETIONS - Safe additive routes
"""

from flask import Blueprint, jsonify, request
import logging
from jarvis_auto_voice_listener import get_auto_listener, init_auto_listener

logger = logging.getLogger("jarvis.auto_voice_routes")

# Create blueprint
auto_voice_bp = Blueprint('auto_voice', __name__, url_prefix='/api/jarvis/auto-voice')

def register_auto_voice_routes(app):
    """Register auto-voice routes to Flask app"""
    app.register_blueprint(auto_voice_bp)
    logger.info("Auto-voice routes registered")


@auto_voice_bp.route('/status', methods=['GET'])
def get_auto_voice_status():
    """Get current auto-listening status"""
    try:
        listener = get_auto_listener()
        status = listener.get_status()
        
        return jsonify({
            'success': True,
            'status': status,
            'message': 'Auto-voice status retrieved'
        }), 200
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@auto_voice_bp.route('/enable', methods=['POST'])
def enable_auto_listening():
    """Enable automatic listening"""
    try:
        listener = get_auto_listener()
        listener.enable_listening()
        
        return jsonify({
            'success': True,
            'message': 'Auto-listening enabled',
            'status': listener.get_status()
        }), 200
    except Exception as e:
        logger.error(f"Error enabling listening: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@auto_voice_bp.route('/disable', methods=['POST'])
def disable_auto_listening():
    """Disable automatic listening"""
    try:
        listener = get_auto_listener()
        listener.disable_listening()
        
        return jsonify({
            'success': True,
            'message': 'Auto-listening disabled',
            'status': listener.get_status()
        }), 200
    except Exception as e:
        logger.error(f"Error disabling listening: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@auto_voice_bp.route('/toggle-restart', methods=['POST'])
def toggle_auto_restart():
    """Toggle auto-restart feature"""
    try:
        listener = get_auto_listener()
        listener.toggle_auto_restart()
        
        return jsonify({
            'success': True,
            'message': f"Auto-restart {'enabled' if listener.auto_restart_enabled else 'disabled'}",
            'status': listener.get_status()
        }), 200
    except Exception as e:
        logger.error(f"Error toggling restart: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@auto_voice_bp.route('/reset-stats', methods=['POST'])
def reset_auto_voice_stats():
    """Reset listening statistics"""
    try:
        listener = get_auto_listener()
        listener.reset_stats()
        
        return jsonify({
            'success': True,
            'message': 'Statistics reset',
            'status': listener.get_status()
        }), 200
    except Exception as e:
        logger.error(f"Error resetting stats: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@auto_voice_bp.route('/record-command', methods=['POST'])
def record_command():
    """Record a recognized voice command"""
    try:
        listener = get_auto_listener()
        listener.on_command_recognized()
        
        data = request.get_json() or {}
        transcript = data.get('transcript', 'Unknown command')
        
        logger.info(f"Recorded command: {transcript}")
        
        return jsonify({
            'success': True,
            'message': 'Command recorded',
            'total_commands': listener.total_commands_recognized
        }), 200
    except Exception as e:
        logger.error(f"Error recording command: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@auto_voice_bp.route('/health', methods=['GET'])
def auto_voice_health():
    """Check auto-voice system health"""
    try:
        listener = get_auto_listener()
        
        health = {
            'system': 'operational',
            'listening_enabled': listener.listening_enabled,
            'is_listening': listener.is_currently_listening,
            'auto_restart': listener.auto_restart_enabled,
            'sessions': listener.total_listening_sessions,
            'commands': listener.total_commands_recognized
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
