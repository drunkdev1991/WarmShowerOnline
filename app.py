import os
import json
import random
import string
import time
from collections import defaultdict
from flask import Flask, request, jsonify, Response, render_template
from flask_cors import CORS
import redis
import gevent
from gevent.queue import Queue
from gevent import pywsgi
from geventwebsocket.handler import WebSocketHandler

app = Flask(__name__, static_folder=None)
CORS(app)

# --------------------------
# 1) Redis Config
# --------------------------
REDIS_HOST = os.environ.get('REDIS_HOST', 'redis')
REDIS_PORT = int(os.environ.get('REDIS_PORT', '6379'))
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

SESSION_TTL_SECONDS = 3600  # 1 hour (adjust as needed)

# SSE subscriptions: dict[(code, participantId)] -> set(Queue)
user_sse_subs = defaultdict(set)

# --------------------------
# 2) Helper Functions
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
        return None
    return json.loads(raw)

def save_session_data(code, data):
    """Save session data back to Redis, renewing TTL."""
    session_key = f"session:{code}"
    redis_client.setex(session_key, SESSION_TTL_SECONDS, json.dumps(data))

# --------------------------
# 3) Routes
# --------------------------
@app.route('/')
def index():
    """Serve index.html from templates."""
    return render_template('index.html')

@app.route('/api/session', methods=['POST'])
def create_session():
    """
    Create a new ephemeral session with a random code.
    We do not necessarily need an admin code for this scenario,
    but you can add one if needed.
    """
    code = generate_code()
    session_data = {
        "created_at": time.time(),
        "participants": {},   # participantId -> displayName
        "messages": []        # list of { id, from, to, text, timestamp }
    }
    save_session_data(code, session_data)
    return jsonify({
        "code": code,
        "expires_in_seconds": SESSION_TTL_SECONDS
    })

@app.route('/api/session/<code>/join', methods=['POST'])
def join_session(code):
    """Join an existing session; assign a participantId and store displayName."""
    sdata = get_session_data(code)
    if not sdata:
        return jsonify({"error": "Session not found or expired"}), 404

    participant_id = generate_participant_id()
    display_name = request.json.get('displayName') or participant_id

    sdata["participants"][participant_id] = display_name
    save_session_data(code, sdata)

    return jsonify({
        "participantId": participant_id,
        "displayName": display_name
    })

@app.route('/api/session/<code>/participants', methods=['GET'])
def get_participants(code):
    """Return the list of participants (ID->Name)."""
    sdata = get_session_data(code)
    if not sdata:
        return jsonify({"error": "Session not found or expired"}), 404
    return jsonify({"participants": sdata["participants"]})

@app.route('/api/session/<code>/message', methods=['POST'])
def post_message(code):
    """
    Post a one-time message from -> to.
    'from' -> participantId of sender
    'to'   -> participantId of receiver
    text   -> the warm shower message
    Must check that there's no existing message from->to in session_data["messages"].
    """
    sdata = get_session_data(code)
    if not sdata:
        return jsonify({"error": "Session not found or expired"}), 404

    from_id = request.json.get('from')
    to_id = request.json.get('to')
    text = (request.json.get('text') or "").strip()

    if not from_id or not to_id or not text:
        return jsonify({"error": "Missing from, to, or text"}), 400

    # Validate participants
    if from_id not in sdata["participants"] or to_id not in sdata["participants"]:
        return jsonify({"error": "Invalid participants"}), 400

    # Check if message from->to already exists
    for msg in sdata["messages"]:
        if msg["from"] == from_id and msg["to"] == to_id:
            return jsonify({"error": "You have already messaged this participant"}), 400

    # Create new message
    msg_id = int(time.time() * 1000)  # or use a separate counter
    new_msg = {
        "id": msg_id,
        "from": from_id,
        "to": to_id,
        "text": text,
        "timestamp": time.time()
    }
    sdata["messages"].append(new_msg)
    save_session_data(code, sdata)

    # Broadcast SSE to the receiver only, omitting "from"
    broadcast_sse_to_user(code, to_id, {
        "text": text,
        "timestamp": new_msg["timestamp"]
    })

    return jsonify({"status": "ok", "message": new_msg})

# --------------------------
# 4) SSE Logic
# --------------------------
@app.route('/api/session/<code>/stream/<participant_id>', methods=['GET'])
def sse_stream(code, participant_id):
    """
    SSE endpoint for a specific participant.
    We'll push only messages addressed to them (omitting 'from').
    """
    sdata = get_session_data(code)
    if not sdata:
        return jsonify({"error": "Session not found or expired"}), 404

    if participant_id not in sdata["participants"]:
        return jsonify({"error": "Invalid participant"}), 400

    # Make a dedicated queue for this user
    q = Queue()
    user_sse_subs[(code, participant_id)].add(q)

    # Send any existing messages that were addressed to them
    for msg in sdata["messages"]:
        if msg["to"] == participant_id:
            q.put({"text": msg["text"], "timestamp": msg["timestamp"]})

    def gen():
        while True:
            payload = q.get()
            yield f"data: {json.dumps(payload)}\n\n"

    return Response(gen(), mimetype='text/event-stream')

def broadcast_sse_to_user(code, participant_id, payload):
    """Push a new message to all SSE queues for (code, participant_id)."""
    if (code, participant_id) in user_sse_subs:
        for q in list(user_sse_subs[(code, participant_id)]):
            q.put(payload)

# --------------------------
# 5) Run & (Optional) Cleanup
# --------------------------
@app.route('/api/session/<code>/end', methods=['POST'])
def end_session(code):
    """
    Optional endpoint to manually end a session early (no admin code needed in this example).
    This will remove the session key from Redis and clear SSE subscriptions.
    """
    sdata = get_session_data(code)
    if not sdata:
        return jsonify({"error": "Session not found or already expired"}), 404

    # Delete from Redis
    redis_client.delete(f"session:{code}")
    # Clear SSE subscriptions
    # We'll remove all participant-based queues for this code
    for (c, pid) in list(user_sse_subs.keys()):
        if c == code:
            user_sse_subs.pop((c, pid), None)

    return jsonify({"status": "session ended"})

if __name__ == '__main__':
    print("Starting Warm Shower on port 5000...")
    server = pywsgi.WSGIServer(('0.0.0.0', 5000), app, handler_class=WebSocketHandler)
    server.serve_forever()
