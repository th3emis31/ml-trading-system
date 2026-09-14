"""
JARVIS Wake Word Detection API Routes - Phase D
Endpoints for controlling wake word detection
"""

from flask import Blueprint, jsonify, request
from jarvis_wake_word_detector import get_wake_word_detector
import logging

logger = logging.getLogger(__name__)

# Create blueprint
wake_word_bp = Blueprint('wake_word', __name__, url_prefix='/api/jarvis/wake-word')


@wake_word_bp.route('/enable', methods=['POST'])
def enable_detection():
    """Enable wake word detection"""
    try:
        detector = get_wake_word_detector()
        result = detector.enable()
        
        if result:
            return jsonify({
                'status': 'success',
                'message': 'Wake word detection enabled',
                'enabled': True,
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to enable detection',
            }), 500
    
    except Exception as e:
        logger.error(f"Enable detection error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e),
        }), 500


@wake_word_bp.route('/disable', methods=['POST'])
def disable_detection():
    """Disable wake word detection"""
    try:
        detector = get_wake_word_detector()
        result = detector.disable()
        
        if result:
            return jsonify({
                'status': 'success',
                'message': 'Wake word detection disabled',
                'enabled': False,
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to disable detection',
            }), 500
    
    except Exception as e:
        logger.error(f"Disable detection error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e),
        }), 500


@wake_word_bp.route('/status', methods=['GET'])
def get_status():
    """Get wake word detector status"""
    try:
        detector = get_wake_word_detector()
        status = detector.get_status()
        
        return jsonify({
            'status': 'success',
            'detector': status,
        })
    
    except Exception as e:
        logger.error(f"Get status error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e),
        }), 500


@wake_word_bp.route('/record', methods=['POST'])
def record_detection():
    """Record wake word detection"""
    try:
        data = request.get_json() or {}
        wake_word = data.get('wake_word', '')
        confidence = data.get('confidence', 0.8)
        
        if not wake_word:
            return jsonify({
                'status': 'error',
                'message': 'wake_word is required',
            }), 400
        
        detector = get_wake_word_detector()
        detection = detector.record_detection(wake_word, confidence)
        
        return jsonify({
            'status': 'success',
            'message': 'Detection recorded',
            'detection': detection,
        })
    
    except Exception as e:
        logger.error(f"Record detection error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e),
        }), 500


@wake_word_bp.route('/validate', methods=['POST'])
def validate_detection():
    """Validate if text contains wake word"""
    try:
        data = request.get_json() or {}
        text = data.get('text', '')
        confidence = data.get('confidence', 0.8)
        
        if not text:
            return jsonify({
                'status': 'error',
                'message': 'text is required',
            }), 400
        
        detector = get_wake_word_detector()
        is_valid, wake_word = detector.validate_detection(text, confidence)
        
        if is_valid:
            detector.record_detection(wake_word, confidence)
        
        return jsonify({
            'status': 'success',
            'is_valid': is_valid,
            'wake_word': wake_word,
            'confidence': float(confidence),
        })
    
    except Exception as e:
        logger.error(f"Validate detection error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e),
        }), 500


@wake_word_bp.route('/sensitivity', methods=['POST'])
def set_sensitivity():
    """Set detection sensitivity"""
    try:
        data = request.get_json() or {}
        sensitivity = data.get('sensitivity', 0.8)
        
        detector = get_wake_word_detector()
        result = detector.set_sensitivity(sensitivity)
        
        if result:
            return jsonify({
                'status': 'success',
                'message': f'Sensitivity set to {sensitivity}',
                'sensitivity': sensitivity,
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to set sensitivity',
            }), 500
    
    except ValueError as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
        }), 400
    
    except Exception as e:
        logger.error(f"Set sensitivity error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e),
        }), 500


@wake_word_bp.route('/add-word', methods=['POST'])
def add_word():
    """Add new wake word"""
    try:
        data = request.get_json() or {}
        word = data.get('word', '')
        
        if not word:
            return jsonify({
                'status': 'error',
                'message': 'word is required',
            }), 400
        
        detector = get_wake_word_detector()
        result = detector.add_wake_word(word)
        
        if result:
            return jsonify({
                'status': 'success',
                'message': f'Wake word added: {word}',
                'wake_words': detector.get_activation_keywords(),
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to add wake word',
            }), 500
    
    except Exception as e:
        logger.error(f"Add word error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e),
        }), 500


@wake_word_bp.route('/remove-word', methods=['POST'])
def remove_word():
    """Remove wake word"""
    try:
        data = request.get_json() or {}
        word = data.get('word', '')
        
        if not word:
            return jsonify({
                'status': 'error',
                'message': 'word is required',
            }), 400
        
        detector = get_wake_word_detector()
        result = detector.remove_wake_word(word)
        
        if result:
            return jsonify({
                'status': 'success',
                'message': f'Wake word removed: {word}',
                'wake_words': detector.get_activation_keywords(),
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to remove wake word',
            }), 500
    
    except Exception as e:
        logger.error(f"Remove word error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e),
        }), 500


@wake_word_bp.route('/history', methods=['GET'])
def get_history():
    """Get detection history"""
    try:
        detector = get_wake_word_detector()
        history = detector.get_detection_history()
        
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


@wake_word_bp.route('/background-listening', methods=['POST'])
def set_background_listening():
    """Enable/disable background listening"""
    try:
        data = request.get_json() or {}
        enabled = data.get('enabled', True)
        
        detector = get_wake_word_detector()
        result = detector.set_background_listening(enabled)
        
        if result:
            return jsonify({
                'status': 'success',
                'message': f'Background listening {"enabled" if enabled else "disabled"}',
                'background_listening': enabled,
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to set background listening',
            }), 500
    
    except Exception as e:
        logger.error(f"Set background listening error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e),
        }), 500


@wake_word_bp.route('/keywords', methods=['GET'])
def get_keywords():
    """Get activation keywords"""
    try:
        detector = get_wake_word_detector()
        keywords = detector.get_activation_keywords()
        
        return jsonify({
            'status': 'success',
            'keywords': keywords,
            'count': len(keywords),
        })
    
    except Exception as e:
        logger.error(f"Get keywords error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e),
        }), 500


@wake_word_bp.route('/reset-stats', methods=['POST'])
def reset_stats():
    """Reset statistics"""
    try:
        detector = get_wake_word_detector()
        detector.reset_stats()
        
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


@wake_word_bp.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    try:
        detector = get_wake_word_detector()
        status = detector.get_status()
        
        return jsonify({
            'status': 'ok',
            'service': 'wake-word-detector',
            'enabled': status.get('enabled', False),
            'timestamp': __import__('datetime').datetime.now(
                __import__('datetime').timezone.utc
            ).isoformat(),
        })
    
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
        }), 500


def register_wake_word_routes(app):
    """Register wake word routes with Flask app"""
    app.register_blueprint(wake_word_bp)
    logger.info("Wake word detection routes registered")
