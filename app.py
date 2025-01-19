import os
import json
import random
import string
import time
from datetime import datetime, timedelta
from collections import defaultdict, deque

from flask import Flask, request, jsonify, Response, render_template
from flask_cors import CORS
import redis
import gevent
from gevent.queue import Queue

app = Flask(__name__, static_folder=None)
CORS(app)

# ------------------------------------------------------------------------
# 1) Redis Connection Setup
# ------------------------------------------------------------------------
REDIS_HOST = os.environ.get('REDIS_HOST', 'redis')
REDIS_PORT = int(os.environ.get('REDIS_PORT', '6379'))
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

SESSION_TTL_SECONDS = 3600  # e.g. 1 hour

# ------------------------------------------------------------------------
# 2) SSE Connections per session code (in-memory)
# ------------------------------------------------------------------------
# For each session code, we'll keep a set of subscribers, each a Queue.
subscribers = defaultdict(set)

def generate_code(length=6, numeric=True):
    """Generate a random code (6-digit numeric by default)."""
    chars = string.digits if numeric else (string.ascii_letters + string.digits)
    return ''.join(random.choices(chars, k=length))

def generate_participant_id():
    """Generate a short random ID for participants."""
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

@app.route('/')
def index():
    """Serve a minimal homepage if someone visits root."""
    return render_template('index.html')

# ------------------------------------------------------------------------
# 3) Session Management
# ------------------------------------------------------------------------
@app.route('/api/session', methods=['POST'])
def create_session():
    """
    Creates a new ephemeral session with:
      - A 6-digit session code
      - A separate admin code (4-digit) for privileged actions
    Stored in Redis with a TTL.
    Returns JSON: { code, adminCode, expires_in_seconds }
    """
    code = generate_code()  # e.g. "123456"
    admin_code = generate_code(4)  # e.g. "7890"
    session_key = f"session:{code}"

    session_data = {
        "created_at": time.time(),
        "admin_code": admin_code,
        "messages": [],
        "participants": {}
    }
    # Store in Redis (JSON), set a TTL
    redis_client.setex(session_key, SESSION_TTL_SECONDS, json.dumps(session_data))

    return jsonify({
        "code": code,
        "adminCode": admin_code,
        "expires_in_seconds": SESSION_TTL_SECONDS
    })

@app.route('/api/session/<code>/join', methods=['POST'])
def join_session(code):
    """Join an existing session with the given code, returns participantId."""
    session_key = f"session:{code}"
    data = redis_client.get(session_key)
    if not data:
        return jsonify({"error": "Session not found or expired"}), 404

    session_data = json.loads(data)

    participant_id = generate_participant_id()
    display_name = request.json.get('displayName') or participant_id
    session_data["participants"][participant_id] = display_name

    # Persist updated session (renew TTL)
    redis_client.setex(session_key, SESSION_TTL_SECONDS, json.dumps(session_data))

    return jsonify({
        "participantId": participant_id,
        "displayName": display_name
    })

# ------------------------------------------------------------------------
# 4) Posting & Deleting Messages
# ------------------------------------------------------------------------
@app.route('/api/session/<code>/message', methods=['POST'])
def post_message(code):
    """Post a new warm shower message."""
    session_key = f"session:{code}"
    data = redis_client.get(session_key)
    if not data:
        return jsonify({"error": "Session not found or expired."}), 404

    session_data = json.loads(data)

    participant_id = request.json.get('participantId')
    text = request.json.get('text', '').strip()
    if not participant_id or not text:
        return jsonify({"error": "Missing participantId or text"}), 400

    display_name = session_data["participants"].get(participant_id, "Unknown")
    message_obj = {
        "participantId": participant_id,
        "displayName": display_name,
        "text": text,
        "timestamp": time.time()
    }
    session_data["messages"].append(message_obj)

    # Update in Redis
    redis_client.setex(session_key, SESSION_TTL_SECONDS, json.dumps(session_data))

    # Notify SSE subscribers
    broadcast_sse(code, message_obj)

    return jsonify({"status": "ok", "message": message_obj})

@app.route('/api/session/<code>/message/<int:msg_index>', methods=['DELETE'])
def delete_message(code, msg_index):
    """
    Delete a message by its index in the session's message list.
    Requires ?adminCode=xxxx
    """
    admin_code = request.args.get('adminCode')
    if not admin_code:
        return jsonify({"error": "adminCode is required"}), 400

    session_key = f"session:{code}"
    data = redis_client.get(session_key)
    if not data:
        return jsonify({"error": "Session not found or expired."}), 404

    session_data = json.loads(data)

    if session_data["admin_code"] != admin_code:
        return jsonify({"error": "Invalid admin code"}), 403

    messages_list = session_data.get("messages", [])
    if msg_index < 0 or msg_index >= len(messages_list):
        return jsonify({"error": "Invalid message index"}), 400

    # Remove the message
    removed_msg = messages_list.pop(msg_index)
    # Update in Redis
    redis_client.setex(session_key, SESSION_TTL_SECONDS, json.dumps(session_data))

    return jsonify({"deleted": removed_msg})

# ------------------------------------------------------------------------
# 5) SSE Streaming
# ------------------------------------------------------------------------
# We'll store a set of subscriber queues per session code.
# Each queue receives new messages (via broadcast_sse).

@app.route('/api/session/<code>/stream', methods=['GET'])
def sse_stream(code):
    """
    SSE endpoint: Clients connect to get real-time messages.
    We'll keep a subscriber queue, yield events as they come in.
    """
    # Check if session is valid
    session_key = f"session:{code}"
    data = redis_client.get(session_key)
    if not data:
        return jsonify({"error": "Session not found or expired"}), 404

    # Create a queue for this client
    q = Queue()
    subscribers[code].add(q)

    # Immediately send existing messages as "catch-up"
    session_data = json.loads(data)
    for msg in session_data["messages"]:
        q.put(msg)

    def gen():
        # Stream data from the queue to the client
        while True:
            # If queue empty, block until new message arrives
            msg = q.get()
            yield f"data: {json.dumps(msg)}\n\n"

    # Return a streaming response
    return Response(gen(), mimetype='text/event-stream')

def broadcast_sse(code, message_obj):
    """
    Push newly posted messages to all SSE subscriber queues for `code`.
    """
    if code not in subscribers:
        return
    for q in list(subscribers[code]):
        q.put(message_obj)

# ------------------------------------------------------------------------
# 6) End Session (Manual)
# ------------------------------------------------------------------------
@app.route('/api/session/<code>/end', methods=['POST'])
def end_session(code):
    """
    Manually end the session. Requires ?adminCode=xxxx
    - Removes the session key from Redis
    - Clears SSE subscribers
    """
    admin_code = request.args.get('adminCode')
    if not admin_code:
        return jsonify({"error": "adminCode is required"}), 400

    session_key = f"session:{code}"
    data = redis_client.get(session_key)
    if not data:
        return jsonify({"error": "Session not found or already expired."}), 404

    session_data = json.loads(data)
    if session_data["admin_code"] != admin_code:
        return jsonify({"error": "Invalid admin code"}), 403

    # Delete from Redis
    redis_client.delete(session_key)
    # Clear SSE subscribers
    subscribers.pop(code, None)

    return jsonify({"status": "session ended"})

# ------------------------------------------------------------------------
# 7) Running the App
# ------------------------------------------------------------------------
if __name__ == '__main__':
    # Use gevent for concurrency (enables streaming better)
    from gevent import pywsgi
    from geventwebsocket.handler import WebSocketHandler

    port = 5000
    print(f"Running server on port {port}...")
    server = pywsgi.WSGIServer(('0.0.0.0', port), app, handler_class=WebSocketHandler)
    server.serve_forever()
