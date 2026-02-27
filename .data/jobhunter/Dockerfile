FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV JOBHUNTER_DB=/data/jobhunter.db

EXPOSE 5050

CMD ["python", "app.py"]
