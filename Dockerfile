FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY goal_to_parameters/ ./goal_to_parameters/
COPY evaluation/stage1_kpis/ ./evaluation/stage1_kpis/
COPY evaluation/simod_outputs/bpic2017/simod_raw.json ./evaluation/simod_outputs/bpic2017/simod_raw.json
COPY evaluation/simod_outputs/bpic2012/simod_raw.json ./evaluation/simod_outputs/bpic2012/simod_raw.json
COPY evaluation/simod_outputs/sepsis/simod_raw.json ./evaluation/simod_outputs/sepsis/simod_raw.json

ENV PYTHONUNBUFFERED=1

EXPOSE 8501

CMD ["streamlit", "run", "goal_to_parameters/app.py", "--server.address=0.0.0.0", "--server.port=8501"]
