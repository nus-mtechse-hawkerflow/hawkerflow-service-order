FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /usr/local/app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY src/ .
COPY resources ./resources

EXPOSE 8080

RUN useradd app
USER app

CMD ["python3", "main.py"]
