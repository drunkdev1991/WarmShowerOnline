import os
import json
import random
import string
import time
import logging
from collections import defaultdict

from flask import Flask, request, jsonify, Response, render_template, send_from_directory
from flask_cors import CORS
import redis
import gevent
from gevent.queue import Queue
from gevent import pywsgi
from geventwebsocket.handler import WebSocketHandler

# --------------------------
# 1) Setup Logging
# --------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder=None)
CORS(app)

# --------------------------
# 2) Redis Config
# --------------------------
REDIS_HOST = os.environ.get('REDIS_HOST', 'redis')
REDIS_PORT = int(os.environ.get('REDIS_PORT', '6379'))
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

SESSION_TTL_SECONDS = 3600  # e.g. 1 hour

# SSE subscriptions: dict[(code, participantId)] -> set(Queue)
user_sse_subs = defaultdict(set)

# --------------------------
# 3) Logging Decorator for Requests
# --------------------------
@app.before_request
def log_request_info():
    logger.info("Incoming request: %s %s", request.method, request.path)

# --------------------------
# 4) Helper Functions
# --------------------------
def generate_code(length=6, numeric=True):
    """Generate a random code (default 6-digit numeric)."""
    chars = string.digits if numeric else (string.ascii_letters + string.digits)
    return ''.join(random.choices(chars, k=length))

def generate_participant_id():
    """Generate an 8-char random ID for participants."""
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

def get_session_data(code):
    """Fetch the session data from Redis; return None if not found."""
    session_key = f"session:{code}"
    raw = redis_client.get(session_key)
    if not raw:
        logger.debug("Session %s not found or expired.", code)
        return None
    return json.loads(raw)

def save_session_data(code, data):
    """Save session data back to Redis, renewing TTL."""
    session_key = f"session:{code}"
    redis_client.setex(session_key, SESSION_TTL_SECONDS, json.dumps(data))
    logger.debug("Saved session %s data, set TTL = %s", code, SESSION_TTL_SECONDS)

# --------------------------
# 5) Routes
# --------------------------
# TODO: fix serving
@app.route('/<path:path>')
def index():
    logger.info("Serving static files")
    return send_from_directory('frontend/dist', path)


# Route for privacy-policy.html
@app.route('/privacy-policy.html')
def privacy_policy():
    return render_template('privacy-policy.html')

@app.route('/api/session', methods=['POST'])
def create_session():
    """
    Create a new ephemeral session with a random code.
    """
    code = generate_code()
    session_data = {
        "created_at": time.time(),
        "participants": {},   # participantId -> displayName
        "messages": []        # list of { id, from, to, text, timestamp }
    }
    save_session_data(code, session_data)
    logger.info("Created new session %s", code)
    return jsonify({
        "code": code,
        "expires_in_seconds": SESSION_TTL_SECONDS
    })

@app.route('/api/session/<code>/join', methods=['POST'])
def join_session(code):
    """Join an existing session; assign a participantId and store displayName."""
    logger.info("Join request for session %s", code)
    sdata = get_session_data(code)
    if not sdata:
        logger.warning("Session %s not found or expired for join.", code)
        return jsonify({"error": "Session not found or expired"}), 404

    participant_id = generate_participant_id()
    display_name = request.json.get('displayName') or participant_id

    sdata["participants"][participant_id] = display_name
    save_session_data(code, sdata)

    logger.info("Participant %s (%s) joined session %s", participant_id, display_name, code)
    return jsonify({
        "participantId": participant_id,
        "displayName": display_name
    })

@app.route('/api/session/<code>/participants', methods=['GET'])
def get_participants(code):
    """Return the list of participants (ID->Name)."""
    logger.info("Getting participants for session %s", code)
    sdata = get_session_data(code)
    if not sdata:
        logger.warning("Session %s not found or expired when fetching participants.", code)
        return jsonify({"error": "Session not found or expired"}), 404

    return jsonify({"participants": sdata["participants"]})

@app.route('/api/session/<code>/message', methods=['POST'])
def post_message(code):
    """
    Post a one-time message from -> to, then broadcast SSE to receiver only.
    """
    logger.info("Posting message in session %s", code)
    sdata = get_session_data(code)
    if not sdata:
        logger.warning("Session %s not found or expired when posting message.", code)
        return jsonify({"error": "Session not found or expired"}), 404

    from_id = request.json.get('from')
    to_id = request.json.get('to')
    text = (request.json.get('text') or "").strip()

    if not from_id or not to_id or not text:
        logger.warning("Invalid payload from=%s to=%s text=%s", from_id, to_id, text)
        return jsonify({"error": "Missing from, to, or text"}), 400

    if from_id not in sdata["participants"] or to_id not in sdata["participants"]:
        logger.warning("Invalid participant: from=%s to=%s not in session data.", from_id, to_id)
        return jsonify({"error": "Invalid participants"}), 400

    # check if a message from->to already exists
    for msg in sdata["messages"]:
        if msg["from"] == from_id and msg["to"] == to_id:
            logger.info("Duplicate message from %s to %s in session %s", from_id, to_id, code)
            return jsonify({"error": "You have already messaged this participant"}), 400

    # create new message
    msg_id = int(time.time() * 1000)
    new_msg = {
        "id": msg_id,
        "from": from_id,
        "to": to_id,
        "text": text,
        "timestamp": time.time()
    }
    sdata["messages"].append(new_msg)
    save_session_data(code, sdata)

    logger.info("New message from %s to %s in session %s", from_id, to_id, code)
    # broadcast SSE to the receiver only
    broadcast_sse_to_user(code, to_id, {
        "text": text,
        "timestamp": new_msg["timestamp"]
    })

    return jsonify({"status": "ok", "message": new_msg})

@app.route('/api/session/<code>/stream/<participant_id>', methods=['GET'])
def sse_stream(code, participant_id):
    """
    SSE endpoint for a specific participant.
    We do NOT push old messages, only new ones going forward.
    """
    logger.info("SSE subscription request for session=%s participant=%s", code, participant_id)
    sdata = get_session_data(code)
    if not sdata:
        logger.warning("Session %s not found or expired for SSE subscription.", code)
        return jsonify({"error": "Session not found or expired"}), 404

    if participant_id not in sdata["participants"]:
        logger.warning("Invalid participant %s for SSE in session %s", participant_id, code)
        return jsonify({"error": "Invalid participant"}), 400

    # Create a dedicated queue for this user
    q = Queue()
    user_sse_subs[(code, participant_id)].add(q)
    logger.info("Added SSE subscriber queue for session=%s participant=%s", code, participant_id)

    def gen():
        while True:
            payload = q.get()  # blocks until new message
            logger.debug("SSE event to participant=%s in session=%s => %s", participant_id, code, payload)
            yield f"data: {json.dumps(payload)}\n\n"

    return Response(gen(), mimetype='text/event-stream')

def broadcast_sse_to_user(code, participant_id, payload):
    """
    Push a new message to all SSE queues for (code, participant_id).
    """
    logger.debug("Broadcasting SSE to participant=%s session=%s payload=%s", participant_id, code, payload)
    if (code, participant_id) in user_sse_subs:
        for q in list(user_sse_subs[(code, participant_id)]):
            q.put(payload)

@app.route('/api/session/<code>/end', methods=['POST'])
def end_session(code):
    """
    Optional endpoint to manually end a session early.
    Removes the session from Redis and clears SSE subscriptions.
    """
    logger.info("Ending session %s", code)
    sdata = get_session_data(code)
    if not sdata:
        logger.warning("Session %s not found or already expired, cannot end.", code)
        return jsonify({"error": "Session not found or already expired"}), 404

    # delete from redis
    redis_client.delete(f"session:{code}")
    # clear SSE subscriptions
    for (c, pid) in list(user_sse_subs.keys()):
        if c == code:
            user_sse_subs.pop((c, pid), None)
    logger.info("Session %s ended, data removed from Redis.", code)
    return jsonify({"status": "session ended"})

# --------------------------
# 6) Run
# --------------------------
if __name__ == '__main__':
    logger.info("Starting Warm Shower app on port 5000...")
    server = pywsgi.WSGIServer(('0.0.0.0', 5000), app, handler_class=WebSocketHandler)
    server.serve_forever()
