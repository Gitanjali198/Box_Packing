"""Gunicorn production configuration optimized for Railway / container environments."""
import os

# Railway injects the port dynamically (default 8080)
port = os.environ.get("PORT", "8080")
bind = f"0.0.0.0:{port}"

# Railway Free/Starter tier has a 512 MB memory limit.
# Using 1 worker with lightweight threads keeps base memory footprint strictly at ~55 MB.
workers = 1
threads = 4
worker_class = "gthread"

# Timeout: Allow up to 180 seconds for 3D packing calculations
timeout = 180
keepalive = 5

# Memory protection: recycle worker after 1000 requests to prevent memory leaks
max_requests = 1000
max_requests_jitter = 50

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"
