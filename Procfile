web: uvicorn sharp_tracker.live:app --host 0.0.0.0 --port $PORT
ingest: uvicorn sharp_tracker.api:app --host 0.0.0.0 --port $PORT
worker: python -m sharp_tracker.collector
