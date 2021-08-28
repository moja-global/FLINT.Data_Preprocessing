FROM python:3.7

WORKDIR /app

COPY . .

RUN pip install .

CMD ["flintdata", "--help"]
