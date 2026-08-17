web: streamlit run streamlit_app.py --server.port $PORT --server.address 0.0.0.0
ingest: uvicorn sharp_tracker.api:app --host 0.0.0.0 --port $PORT
worker: python -m sharp_tracker.collector
