FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .
RUN addgroup --system julabo && adduser --system --ingroup julabo julabo && chown -R julabo:julabo /app
USER julabo
EXPOSE 8765 9100
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python3 -c "import socket; s=socket.create_connection(('127.0.0.1',8765),2); s.close()"
ENTRYPOINT ["julabo-server"]
