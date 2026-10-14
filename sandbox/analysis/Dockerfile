ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY requirements.lock /opt/drov/requirements.lock
COPY wheels/ /opt/drov/wheels/
RUN python3 -m pip install --disable-pip-version-check --no-cache-dir --no-index \
      --find-links=/opt/drov/wheels --require-hashes -r /opt/drov/requirements.lock \
    && rm -rf /opt/drov/wheels
USER 65534:65534
