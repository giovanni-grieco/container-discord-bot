FROM python:3.13-alpine

COPY requirements.txt requirements.txt

RUN pip install --no-cache-dir -r requirements.txt

COPY main.py main.py

ENTRYPOINT [ "python3", "main.py" ]