# One-to-One Anonymous Warm Shower

This repository hosts a **lightweight, ephemeral** web application for sending positive, anonymous messages (a.k.a. _warm showers_) among team members. Each participant can:
- Join a short-lived session via a unique code
- See a list of other participants who have joined
- Send exactly **one** anonymous message per other participant
- Receive messages in real time, without knowing the sender’s identity
- End the session at any time, purging all data

The solution is designed to respect **data minimization** (no personal data stored beyond optional display names) and to handle ephemeral usage via **Redis** with TTL. When the session expires or ends, all messages and participants are cleared.

## Features

1. **Ephemeral Sessions**  
   Each session is identified by a 6-digit code. Data is stored in Redis with a time-to-live (TTL), so sessions self-destruct after a certain period.

2. **No History for Late Joiners**  
   A participant sees **only** messages that arrive **after** they join, avoiding older/backlog messages.

3. **One-to-One Messaging**  
   Each user can send one anonymous message to each other participant. The recipient sees “Anonymous” and the message text.

4. **Real-Time Updates**  
   The app uses **Server-Sent Events (SSE)** to deliver new messages only to the intended receiver.

5. **Completion Alert**  
   After a user has sent a warm shower message to **all** other participants, a “Thank you for participation!” alert pops up.

6. **No-Auth Simplicity**  
   Users only need a session code. Each is assigned a random `participantId` automatically, plus an optional display name.

## Project Structure

```
my-warmshower/
├── app.py                # Main Flask + SSE backend
├── requirements.txt      # Python dependencies
├── Dockerfile            # Docker build config
├── docker-compose.yml    # Multi-container stack (Flask + Redis)
└── templates/
    └── index.html        # Alpine.js frontend
```

### Key Files

- **`app.py`**: Implements the Flask server, routes for session creation, joining, sending messages, and SSE streaming. Also includes logging for debug.  
- **`index.html`**: The minimal one-page UI. Uses [Alpine.js](https://alpinejs.dev/) for reactivity.  
- **`requirements.txt`**: Python dependencies (Flask, Redis, gevent, etc.).  
- **`docker-compose.yml`**: Spawns two containers:
  1. **`backend`** (Flask app)  
  2. **`redis`** (in-memory store for ephemeral session data)

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) installed on your machine  
- [Docker Compose plugin](https://docs.docker.com/compose/install/) or older standalone `docker-compose`  
- Ports **5000** and **6379** available locally

## Quick Start

1. **Clone or copy** this repository:
   ```bash
   git clone https://github.com/yourusername/my-warmshower.git
   cd my-warmshower
   ```
2. **Build & Run** the containers:
   ```bash
   docker compose build
   docker compose up -d
   ```
3. **Access** the app at [http://localhost:5000](http://localhost:5000).

## Usage

1. **Create Session**  
   - On the home page, click **“Create Session”**.  
   - A session code (e.g., `123456`) is generated.  
   - Share this code with your teammates.

2. **Join Session**  
   - Enter the same session code in the **“Join Session”** area.  
   - Optionally set a display name (otherwise a random ID is used).  

3. **Send Messages**  
   - Once joined, you see a list of other participants who have also joined.  
   - Click **“Send Warm Shower”** next to each participant you want to message.  
   - Type your message in the prompt. The other participant receives it as “Anonymous.”

4. **Receive Messages**  
   - Whenever someone sends a message to you, it appears under “Messages You Have Received.”

5. **Completion**  
   - Once you have sent a message to **all** other participants, the UI pops up a “Thank you for participation!” message.

6. **End Session**  
   - Optionally, click **“End Session”** to manually purge it early (otherwise it expires via TTL in Redis).

## Logs & Debugging

- **Server logs**:  
  ```bash
  docker compose logs -f backend
  ```
  You’ll see request logs, SSE subscriptions, message events, etc.
- **Client logs**:  
  Open your browser’s **DevTools** → **Console**. The frontend code logs key events (`[Client] ...`).

## Environment Variables

- `REDIS_HOST`: Hostname for the Redis container (default: `redis`)  
- `REDIS_PORT`: Port for Redis (default: `6379`)  
- `SESSION_TTL_SECONDS`: Set in `app.py` for how long sessions last (default: `3600`, i.e. 1 hour)

## Customization

- **TTL**: Edit `SESSION_TTL_SECONDS` in `app.py` to change the default ephemeral lifetime.  
- **UI**: Modify `templates/index.html` to customize the design or add features (like CAPTCHAs, etc.).  
- **Security/Hardening**:  
  - Rate-limit or filter messages if needed.  
  - Use a reverse proxy with HTTPS in production (e.g., Traefik or Nginx + Let’s Encrypt).

## License

[GNU/GPLv3](https://www.gnu.org/licenses/gpl-3.0.txt) - you may use this piece of mastery as you desire. Coffee is appreciated. 

### Author 

- [@drunkdev1991](https://github.com/drunkdev1991)
  Feel free to reach out with questions or suggestions.
- chatGPT o1 as executive developer

### Contributing

1. Fork the repo  
2. Create a new branch  
3. Make changes and submit a PR  

Feedback, bug reports, and feature requests are welcome!
