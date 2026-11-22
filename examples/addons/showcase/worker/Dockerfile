# The showcase worker: a standard-library HTTP server (bin/deploy builds it for a site addon).
FROM python:3.14-slim
WORKDIR /worker
COPY server.py .
ENV PORT=8092
EXPOSE 8092
USER 1001
CMD ["python", "server.py"]
