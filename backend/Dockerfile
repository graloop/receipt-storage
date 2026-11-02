FROM python:3.11-slim

# tzdata: the server's clock follows TZ (set in docker-compose.yml), so a receipt
# added at 9 pm in Montreal doesn't count as tomorrow's.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data

WORKDIR /app

COPY requirements.txt .
# pip downloads every package from files.pythonhosted.org. A DNS ad blocker
# (Pi-hole) that answers 0.0.0.0 for it makes pip fail with only "exit code: 1",
# so check it first: this message names the cause.
RUN python -c "import socket, sys; sys.exit(socket.gethostbyname('files.pythonhosted.org').startswith('0.') and 'files.pythonhosted.org is blocked by the DNS (Pi-hole?): allow it')"
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY frontend ./frontend

# Not root: the app only needs to read its own files and write to /data. A new
# Docker volume takes the owner of the folder it's mounted on.
RUN useradd --system --no-create-home receipts && mkdir /data && chown receipts /data
USER receipts

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
