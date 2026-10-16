# One image for the twin setup job, the simulator and the four modules.
# Build context: the mine/ directory.
FROM docker.io/library/python:3.12.7-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt

# The code is the Python package `mine`, laid out as in the repository.
COPY __init__.py mine/
COPY common mine/common
COPY model mine/model
COPY sim mine/sim
COPY modules mine/modules
COPY tests mine/tests

USER nobody
CMD ["python", "-m", "mine.sim"]
