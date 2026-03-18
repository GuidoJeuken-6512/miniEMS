ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install build dependencies for Python packages
RUN apk add --no-cache \
    gcc \
    musl-dev \
    libffi-dev

# Copy and install Python dependencies
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy application source
COPY rootfs/usr/bin/ /usr/bin/

ENTRYPOINT ["python3", "/usr/bin/main.py"]
